"""Deterministic VSS-to-road map matching.

Confidence is deliberately conservative: geometry starts at 0.95 and falls linearly with
distance to the configured cutoff; indistinguishable opposite carriageways subtract 0.20
when no heading resolves them. A street-name match is 0.65, and a sensor-only approach is
0.45. Every match assumes lane 0 because VSS does not provide lane-grade positioning; the
assumption is explicit in ``notes`` and no lateral-offset inference is attempted.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.models.domain import GeoPoint
from app.simulation.network import RoadNetwork, SegmentInfo

_SUFFIXES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "boulevard": "blvd", "blvd": "blvd",
    "road": "rd", "rd": "rd", "drive": "dr", "dr": "dr", "place": "pl", "pl": "pl",
}


@dataclass
class MatchResult:
    segment_id: str | None = None
    position_m: float | None = None
    lanes: list[int] = field(default_factory=list)
    intersection_id: str | None = None
    distance_m: float | None = None
    method: str | None = None
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    reason: str | None = None
    point: GeoPoint | None = None
    lane_assumed: bool = True


class RoadMatcher:
    def __init__(self, network: RoadNetwork, max_distance_m: float = 40.0):
        self.network = network
        self.max_distance_m = max_distance_m
        self._sensor_intersections: dict[str, str] = {}

    def register_sensor(self, sensor_id: str, intersection_id: str | None) -> None:
        if intersection_id:
            self._sensor_intersections[sensor_id] = intersection_id

    def match(
        self,
        *,
        point: GeoPoint | None = None,
        place: str | None = None,
        sensors: list[str] | None = None,
        heading_deg: float | None = None,
    ) -> MatchResult:
        """Match by geometry, then place, then a registered sensor. Never raises."""
        try:
            if point is not None:
                result = self._geometry(point, heading_deg)
                if result.segment_id is not None:
                    return result
            if place:
                result = self._place(place, heading_deg)
                if result.segment_id is not None:
                    return result
            for sensor_id in sensors or []:
                intersection_id = self._sensor_intersections.get(sensor_id)
                if intersection_id:
                    return self._at_intersection(intersection_id, "sensor", heading_deg, 0.45)
            return MatchResult(point=point, reason="no road segment was close enough and no place or sensor resolved")
        except Exception as exc:  # malformed external data must never take down the frame pipeline
            return MatchResult(point=point, reason=f"map matching failed: {exc}")

    def _geometry(self, point: GeoPoint, heading: float | None) -> MatchResult:
        x, y = self.network.projector.to_xy(point.lon, point.lat)
        nearest = self.network.nearest_segments(x, y, limit=8)
        if not nearest or nearest[0].distance_m > self.max_distance_m:
            distance = nearest[0].distance_m if nearest else None
            return MatchResult(point=point, distance_m=distance, reason="reported point is outside the match radius")

        close = [item for item in nearest if item.distance_m <= nearest[0].distance_m + 4.0]
        chosen = nearest[0]
        notes: list[str] = []
        ambiguous = len(close) > 1 and len({self.network.segments[item.segment_id].direction for item in close}) > 1
        if heading is not None and len(close) > 1:
            chosen = min(close, key=lambda item: (self._heading_delta(self.network.segments[item.segment_id], heading), item.distance_m, item.segment_id))
            notes.append(f"heading {heading:.0f}° selected the carriageway")
        elif ambiguous:
            notes.append("direction ambiguous without a heading; nearest carriageway chosen deterministically")
        notes.append("lane 0 assumed; VSS has no lane-grade position")
        segment = self.network.segments[chosen.segment_id]
        confidence = max(0.05, 0.95 * (1.0 - chosen.distance_m / self.max_distance_m))
        if ambiguous and heading is None:
            confidence = max(0.05, confidence - 0.20)
        return self._result(segment, chosen.position_m, "geometry", confidence, notes, chosen.distance_m, point)

    def _place(self, place: str, heading: float | None) -> MatchResult:
        wanted = _street_set(place)
        matches = [info for info in self.network.intersections.values() if wanted and _street_set(info.name) == wanted]
        if not matches:
            return MatchResult(reason=f"place name '{place}' did not match a network intersection")
        intersection = min(matches, key=lambda info: info.id)
        return self._at_intersection(intersection.id, "place", heading, 0.65)

    def _at_intersection(self, intersection_id: str, method: str, heading: float | None, confidence: float) -> MatchResult:
        intersection = self.network.intersections.get(intersection_id)
        if intersection is None or not intersection.approaches_by_segment:
            return MatchResult(reason=f"{method} resolved to an intersection with no incoming road approach")
        segments = [self.network.segments[sid] for sid in intersection.approaches_by_segment]
        notes: list[str] = []
        if heading is not None:
            segment = min(segments, key=lambda item: (self._heading_delta(item, heading), item.id))
            notes.append(f"heading {heading:.0f}° selected the incoming approach")
        else:
            segment = min(segments, key=lambda item: item.id)
            confidence = max(0.05, confidence - 0.15)
            notes.append("incoming approach ambiguous without a heading; selected deterministically")
        notes.append("lane 0 assumed; VSS has no lane-grade position")
        position = max(0.0, segment.length - 20.0)
        point = self.network.projector.to_point(*self.network.point_along(segment.id, position))
        return self._result(segment, position, method, confidence, notes, None, point, intersection.id)

    def _result(
        self,
        segment: SegmentInfo,
        position: float,
        method: str,
        confidence: float,
        notes: list[str],
        distance: float | None,
        point: GeoPoint,
        intersection_id: str | None = None,
    ) -> MatchResult:
        if segment.length >= 40.0:
            position = min(max(position, 20.0), segment.length - 20.0)
        else:
            position = segment.length / 2.0
        nearest = intersection_id or (segment.destination if segment.destination in self.network.intersections else segment.source)
        return MatchResult(
            segment_id=segment.id, position_m=position, lanes=[0], intersection_id=nearest,
            distance_m=distance, method=method, confidence=min(1.0, max(0.0, confidence)),
            notes=notes, point=point,
        )

    def _heading_delta(self, segment: SegmentInfo, heading: float) -> float:
        """How far the segment's travel direction is from a reported compass heading, in degrees.

        Both sides are true bearings. The scenario's ``heading_offset_deg`` only rotates displacement
        into NB/SB/EB/WB display labels (``heading_direction``); applying it here would compare a
        label-frame bearing with the true bearing VSS reports and bias Oakland's approaches by 45°.
        """
        (x0, y0), (x1, y1) = segment.centerline[-2], segment.centerline[-1]
        bearing = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
        return abs((bearing - heading + 180) % 360 - 180)


def _street_set(value: str) -> frozenset[str]:
    value = re.sub(r"\b(and|at)\b|[@&]", "|", value.lower())
    streets = []
    for part in value.split("|"):
        words = re.findall(r"[a-z0-9]+", part)
        if not words:
            continue
        words[-1] = _SUFFIXES.get(words[-1], words[-1])
        streets.append(" ".join(words))
    return frozenset(streets)
