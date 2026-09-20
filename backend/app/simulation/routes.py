"""Where a response acts on the map: the responder's path and the diversion detour, as segment ids.

Pure functions over the static ``RoadNetwork`` graph, with no TraCI, so they run anywhere without touching the
live connection. They are a presentation aid for the mini map, not what SUMO drives: SUMO reroutes each vehicle
by its own travel times, so the detour here is the free-flow shortest way round the avoided segments, i.e. where
most diverted traffic will go, not a measured flow. Turn restrictions are ignored.
"""

from __future__ import annotations

import heapq
from collections import defaultdict

from app.agent.base import CandidatePlan
from app.models.domain import Incident, PlanRoutes
from app.simulation.network import RoadNetwork


def _shortest(network: RoadNetwork, start: str, goal: str, banned: frozenset[str]) -> list[str]:
    """Segment ids of the quickest free-flow path from junction ``start`` to junction ``goal`` (empty if none)."""
    if start == goal:
        return []
    outgoing: dict[str, list[str]] = defaultdict(list)
    for segment in network.segments.values():
        if segment.id not in banned:
            outgoing[segment.source].append(segment.id)
    best = {start: 0.0}
    came_from: dict[str, str] = {}  # junction -> the segment that reached it
    queue = [(0.0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if node == goal:
            break
        if cost > best.get(node, float("inf")):
            continue
        for segment_id in outgoing[node]:
            segment = network.segments[segment_id]
            arrive = cost + segment.length / max(segment.speed_limit, 1.0)
            if arrive < best.get(segment.destination, float("inf")):
                best[segment.destination] = arrive
                came_from[segment.destination] = segment_id
                heapq.heappush(queue, (arrive, segment.destination))
    if goal not in came_from:
        return []
    path: list[str] = []
    node = goal
    while node != start:
        segment_id = came_from[node]
        path.append(segment_id)
        node = network.segments[segment_id].source
    return path[::-1]


def ems_route(network: RoadNetwork, station_segment: str, incident_segment: str) -> list[str]:
    """The station's segment, the way to the incident's segment, and that segment."""
    if station_segment not in network.segments or incident_segment not in network.segments:
        return []
    if station_segment == incident_segment:
        return [station_segment]
    middle = _shortest(
        network,
        network.segments[station_segment].destination,
        network.segments[incident_segment].source,
        frozenset({station_segment, incident_segment}),
    )
    if not middle and network.segments[station_segment].destination != network.segments[incident_segment].source:
        return []
    return [station_segment, *middle, incident_segment]


def detour(network: RoadNetwork, avoid: list[str]) -> list[str]:
    """The way round each avoided segment (its start junction to its end junction), without any avoided segment."""
    banned = frozenset(avoid)
    found: list[str] = []
    for segment_id in avoid:
        segment = network.segments.get(segment_id)
        if segment is None:
            continue
        for step in _shortest(network, segment.source, segment.destination, banned):
            if step not in found:
                found.append(step)
    return found


def plan_routes(
    network: RoadNetwork, plan: CandidatePlan, incidents: list[Incident], station_segment: str | None
) -> PlanRoutes:
    """The paths one plan acts on: the incident segments, the responder's path if it has a corridor, the detour."""
    blocked = list(dict.fromkeys(i.location.segment_id for i in incidents))
    ems: list[str] = []
    if plan.corridor is not None and station_segment is not None:
        for segment_id in blocked:
            ems.extend(s for s in ems_route(network, station_segment, segment_id) if s not in ems)
    avoid = [s for action in plan.reroutes for s in action.avoid_segment_ids]
    return PlanRoutes(ems_segments=ems, diversion_segments=detour(network, avoid), blocked_segments=blocked)
