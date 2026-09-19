"""Persistent diversion advisory (DMS sign / navigation alert) for SUMO traffic.

Drivers in the scenario are habitual (``device.rerouting.adaptation-interval=0``): they
never reroute on their own, so a diversion is an explicit response. For each complying
driver the avoided edges get a huge per-vehicle travel time and the vehicle is rerouted
on free-flow times, which sends it around those edges wherever an alternative exists.

The advisory stays active for the rest of the run: it is applied to every vehicle in the
network at activation and to each vehicle that departs afterwards. All state lives here
in Python (SUMO does not keep per-vehicle overrides in snapshots), so build a fresh
instance for every simulation process and after every ``restore_snapshot``.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import traci

from app.models.domain import RerouteAction

# Must match sumo.EMS_TYPE / sumo.CRASH_TYPE (not imported: sumo.py imports this module).
NON_BACKGROUND_TYPES = frozenset({"ems", "crash"})
AVOID_TRAVEL_TIME_S = 1e5
COMPLIANCE_BUCKETS = 1000


@dataclass
class _Advisory:
    avoid: frozenset[str]
    threshold: int  # a vehicle complies when its id bucket is below this
    handled: set[str] = field(default_factory=set)


def _bucket(vehicle_id: str) -> int:
    # stable across processes and runs, unlike hash() (salted) or random (order dependent)
    return zlib.crc32(vehicle_id.encode()) % COMPLIANCE_BUCKETS


class DiversionAdvisory:
    """Reroutes a deterministic share of background vehicles around avoided segments.

    ``vehicle_type`` maps a vehicle id to its vType without a TraCI round trip (e.g.
    ``lambda vid: veh[vid][tc.VAR_TYPE]`` over the subscription results, returning None
    for an unknown id). When it returns None the type is fetched over TraCI instead.
    Compliance is a fixed property of the vehicle id, so several advisories in one run
    address the same drivers, who keep avoiding every earlier advisory's segments.
    """

    def __init__(self, conn: traci.connection.Connection, vehicle_type: Callable[[str], str | None]):
        self._conn = conn
        self._vehicle_type = vehicle_type
        self._advisories: list[_Advisory] = []
        self._diverted: set[str] = set()

    @property
    def is_active(self) -> bool:
        return bool(self._advisories)

    @property
    def total_diverted(self) -> int:
        """Distinct vehicles whose route was changed by any advisory so far."""
        return len(self._diverted)

    def activate(self, action: RerouteAction, vehicle_ids: Iterable[str]) -> int:
        """Start an advisory and apply it to ``vehicle_ids`` (every vehicle now in the network).

        Returns how many of them actually took a different route.
        """
        advisory = _Advisory(frozenset(action.avoid_segment_ids), round(action.compliance * COMPLIANCE_BUCKETS))
        self._advisories.append(advisory)
        return sum(self._divert(vid, advisory) for vid in vehicle_ids)

    def on_departed(self, vehicle_ids: Iterable[str]) -> None:
        """Apply every active advisory to vehicles that entered the network this step."""
        if not self._advisories:
            return
        for vid in vehicle_ids:
            for advisory in self._advisories:
                self._divert(vid, advisory)

    def notes(self) -> list[str]:
        if not self._advisories:
            return []
        n = self.total_diverted
        return [f"{n} vehicle{'' if n == 1 else 's'} diverted over the horizon"]

    def _divert(self, vid: str, advisory: _Advisory) -> bool:
        if not advisory.avoid or vid in advisory.handled or _bucket(vid) >= advisory.threshold:
            return False
        if not self._is_background(vid):
            return False
        advisory.handled.add(vid)

        c = self._conn.vehicle
        try:
            route = c.getRoute(vid)
            # a destination on an avoided segment cannot be avoided
            if advisory.avoid.isdisjoint(route) or route[-1] in advisory.avoid:
                return False
            index = c.getRouteIndex(vid)
            if index < 0:
                return False
            # on a junction the route index still names the edge being left (see _estimate_eta)
            on_junction = c.getRoadID(vid).startswith(":")
            if not on_junction and route[index] in advisory.avoid:
                return False
            if advisory.avoid.isdisjoint(route[index + 1 if on_junction else index :]):
                return False

            for edge in sorted(advisory.avoid):
                c.setAdaptedTraveltime(vid, edge, AVOID_TRAVEL_TIME_S)
            c.rerouteTraveltime(vid, currentTravelTimes=False)
            changed = tuple(c.getRoute(vid)) != tuple(route)
        except traci.TraCIException:
            return False  # the vehicle left the network mid-call
        if changed:
            self._diverted.add(vid)
        return changed

    def _is_background(self, vid: str) -> bool:
        vtype = self._vehicle_type(vid)
        if vtype is None:
            try:
                vtype = self._conn.vehicle.getTypeID(vid)
            except traci.TraCIException:
                return False
        return vtype not in NON_BACKGROUND_TYPES
