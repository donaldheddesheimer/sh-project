"""Static road-network topology and geo projection (sumolib only, no TraCI).

Answers questions like "which approaches does intersection B2 have, and which
signal link indices serve each of them?" for the live simulation, the UI map
geometry, the safety validator and (later) agent tools.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import sumolib

from app.models.domain import (
    ApproachGeometry,
    GeoPoint,
    IntersectionGeometry,
    NetworkGeometry,
    PhaseKind,
    SegmentGeometry,
    SignalPhase,
    SignalProgram,
    StationGeometry,
)
from app.simulation.scenario import Scenario

EARTH_M_PER_DEG_LAT = 110_540.0
EARTH_M_PER_DEG_LON_EQUATOR = 111_320.0


def heading_direction(dx: float, dy: float) -> str:
    """Compass travel direction for a displacement (x east, y north)."""
    if abs(dx) >= abs(dy):
        return "EB" if dx >= 0 else "WB"
    return "NB" if dy >= 0 else "SB"


def phase_kind(state: str) -> PhaseKind:
    if any(c in "Gg" for c in state):
        return PhaseKind.GREEN
    if any(c in "yY" for c in state):
        return PhaseKind.YELLOW
    return PhaseKind.ALL_RED


class GeoProjector:
    """Converts SUMO x/y metres to lon/lat.

    Uses the network's own projection when it has one (e.g. OSM imports);
    synthetic networks are anchored at the scenario's geo origin.
    """

    def __init__(self, net: sumolib.net.Net, origin: GeoPoint):
        self._net = net
        self._geo = net.hasGeoProj()
        self._lat0 = origin.lat
        self._lon0 = origin.lon
        self._m_per_deg_lon = EARTH_M_PER_DEG_LON_EQUATOR * math.cos(math.radians(origin.lat))

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        if self._geo:
            lon, lat = self._net.convertXY2LonLat(x, y)
            return lon, lat
        return self._lon0 + x / self._m_per_deg_lon, self._lat0 + y / EARTH_M_PER_DEG_LAT

    def to_point(self, x: float, y: float) -> GeoPoint:
        lon, lat = self.to_lonlat(x, y)
        return GeoPoint(lat=lat, lon=lon)


@dataclass
class SegmentInfo:
    id: str
    name: str
    source: str
    destination: str
    direction: str
    lanes: int
    length: float
    speed_limit: float
    centerline: list[tuple[float, float]]  # node-to-node x/y


@dataclass
class ApproachInfo:
    segment_id: str
    direction: str  # travel direction of vehicles on the approach
    link_indices: list[int] = field(default_factory=list)
    through_link_indices: list[int] = field(default_factory=list)


@dataclass
class IntersectionInfo:
    id: str
    name: str
    x: float
    y: float
    tls_id: str | None
    approaches: dict[str, ApproachInfo]  # keyed by direction
    outgoing: list[str]


class RoadNetwork:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.net = sumolib.net.readNet(str(scenario.net_file), withPrograms=True)
        self.projector = GeoProjector(self.net, scenario.geo_origin)
        self.segments: dict[str, SegmentInfo] = {}
        self.intersections: dict[str, IntersectionInfo] = {}
        self._base_programs: dict[str, SignalProgram] = {}
        self._build()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        for edge in self.net.getEdges(withInternal=False):
            shape = [tuple(p[:2]) for p in edge.getRawShape()]
            (x0, y0), (x1, y1) = shape[-2], shape[-1]
            self.segments[edge.getID()] = SegmentInfo(
                id=edge.getID(),
                name=edge.getName() or edge.getID(),
                source=edge.getFromNode().getID(),
                destination=edge.getToNode().getID(),
                direction=heading_direction(x1 - x0, y1 - y0),
                lanes=edge.getLaneNumber(),
                length=edge.getLength(),
                speed_limit=edge.getSpeed(),
                centerline=shape,
            )

        for node in self.net.getNodes():
            if node.getType() == "dead_end" or len(node.getIncoming()) < 3:
                continue
            tls_id = node.getID() if node.getType() == "traffic_light" else None
            approaches: dict[str, ApproachInfo] = {}
            for edge in node.getIncoming():
                if edge.getFunction() == "internal":
                    continue
                seg = self.segments[edge.getID()]
                approach = ApproachInfo(segment_id=seg.id, direction=seg.direction)
                for conns in edge.getOutgoing().values():
                    for conn in conns:
                        idx = conn.getTLLinkIndex()
                        if idx is None or idx < 0:
                            continue
                        approach.link_indices.append(idx)
                        if conn.getDirection() == "s":
                            approach.through_link_indices.append(idx)
                approaches[seg.direction] = approach
            self.intersections[node.getID()] = IntersectionInfo(
                id=node.getID(),
                name=self._intersection_name(node),
                x=node.getCoord()[0],
                y=node.getCoord()[1],
                tls_id=tls_id,
                approaches=approaches,
                outgoing=[e.getID() for e in node.getOutgoing()],
            )

        for tls in self.net.getTrafficLights():
            for program_id, program in tls.getPrograms().items():
                self._base_programs[tls.getID()] = SignalProgram(
                    intersection_id=tls.getID(),
                    program_id=program_id,
                    phases=[self.describe_phase(tls.getID(), i, p.duration, p.state) for i, p in enumerate(program.getPhases())],
                )

    def _intersection_name(self, node) -> str:
        north_south, east_west = set(), set()
        for edge in node.getIncoming():
            name = edge.getName()
            if not name:
                continue
            (north_south if self.segments[edge.getID()].direction in ("NB", "SB") else east_west).add(name)
        names = sorted(north_south) + sorted(east_west - north_south)
        return " & ".join(names) if names else node.getID()

    # ---------------------------------------------------------------- queries

    def describe_phase(self, intersection_id: str, index: int, duration: float, state: str) -> SignalPhase:
        kind = phase_kind(state)
        served = [] if kind is PhaseKind.ALL_RED else self.served_directions(intersection_id, state)
        if kind is PhaseKind.ALL_RED:
            label = "All red"
        else:
            label = f"{'/'.join(served) if served else 'turns'} {'green' if kind is PhaseKind.GREEN else 'yellow'}"
        return SignalPhase(index=index, duration=duration, state=state, kind=kind, label=label, served_approaches=served)

    def served_directions(self, intersection_id: str, state: str) -> list[str]:
        """Approaches whose through movement is green or yellow in ``state``."""
        info = self.intersections[intersection_id]
        order = ["NB", "SB", "EB", "WB"]
        served = []
        for direction in order:
            approach = info.approaches.get(direction)
            if approach is None:
                continue
            links = approach.through_link_indices or approach.link_indices
            if any(i < len(state) and state[i] in "GgyY" for i in links):
                served.append(direction)
        return served

    def base_program(self, intersection_id: str) -> SignalProgram | None:
        return self._base_programs.get(intersection_id)

    def nearest_intersection(self, x: float, y: float) -> str | None:
        best, best_d = None, float("inf")
        for info in self.intersections.values():
            d = math.hypot(info.x - x, info.y - y)
            if d < best_d:
                best, best_d = info.id, d
        return best

    def point_along(self, segment_id: str, position_m: float, lateral_m: float = 0.0) -> tuple[float, float]:
        """x/y at a distance along a segment's centreline, shifted right by ``lateral_m``."""
        seg = self.segments[segment_id]
        remaining = position_m
        pts = seg.centerline
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            d = math.hypot(x1 - x0, y1 - y0)
            if remaining <= d or (x1, y1) == pts[-1]:
                t = 0.0 if d == 0 else min(1.0, remaining / d)
                ux, uy = ((x1 - x0) / d, (y1 - y0) / d) if d else (0.0, 0.0)
                return x0 + ux * d * t + uy * lateral_m, y0 + uy * d * t - ux * lateral_m
            remaining -= d
        return pts[-1]

    # --------------------------------------------------------------- geometry

    def geometry(self) -> NetworkGeometry:
        to_ll = self.projector.to_lonlat
        segments = [
            SegmentGeometry(
                id=s.id,
                name=s.name,
                source=s.source,
                destination=s.destination,
                direction=s.direction,
                lanes=s.lanes,
                length=s.length,
                speed_limit=s.speed_limit,
                coordinates=[to_ll(x, y) for x, y in s.centerline],
            )
            for s in self.segments.values()
        ]
        intersections = []
        for info in self.intersections.values():
            approaches = []
            for approach in info.approaches.values():
                seg = self.segments[approach.segment_id]
                # signal head: just before the stop line, on the approach's side of the road
                lateral = seg.lanes * 3.2 * 0.5 + 1.5
                x, y = self.point_along(seg.id, max(0.0, seg.length - 16.0), lateral_m=lateral)
                approaches.append(
                    ApproachGeometry(segment_id=seg.id, approach=approach.direction, signal_point=to_ll(x, y))
                )
            lon, lat = to_ll(info.x, info.y)
            intersections.append(
                IntersectionGeometry(
                    id=info.id, name=info.name, lon=lon, lat=lat, signalized=info.tls_id is not None, approaches=approaches
                )
            )
        stations = []
        for station in self.scenario.ems_stations:
            seg = self.segments[station.edge]
            lon, lat = to_ll(*seg.centerline[0])
            stations.append(StationGeometry(id=station.id, name=station.name, segment_id=seg.id, lon=lon, lat=lat))

        xs = [x for s in self.segments.values() for x, _ in s.centerline]
        ys = [y for s in self.segments.values() for _, y in s.centerline]
        sw, ne = to_ll(min(xs), min(ys)), to_ll(max(xs), max(ys))
        center = to_ll((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        return NetworkGeometry(
            id=self.scenario.id,
            name=self.scenario.name,
            center=center,
            bounds=(sw, ne),
            segments=segments,
            intersections=intersections,
            stations=stations,
        )
