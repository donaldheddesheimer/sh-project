"""The only module that assumes fields in NVIDIA VSS documents.

The live 3.2 Video Analytics MCP reference names ``timestamp``/``end`` and enriches
``info.verdict``; older Blueprint material used ``start`` and nested analytics-module info,
so both shapes are accepted here. Severity is not part of the observed VSS incident contract:
an explicit known severity is honored, otherwise ``VSS_DEFAULT_SEVERITY`` is a modelling
assumption used by the twin, not an observed fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.domain import (
    GeoPoint, Incident, IncidentLocation, IncidentMatch, IncidentStatus, IncidentType, Severity,
)
from app.smart_city.matching import RoadMatcher


@dataclass(frozen=True)
class MappingOutcome:
    incident: Incident | None
    filtered_unconfirmed: bool = False


@dataclass(frozen=True)
class SensorRecord:
    id: str
    name: str
    point: GeoPoint | None
    place: str | None


def unpack_collection(payload: Any, *keys: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in (*keys, "results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if any(key in payload for key in ("id", "sensorId", "place")):
            return [payload]
    return []


def map_vss_document(
    document: dict[str, Any], *, internal_id: str, source: str, sim_time: float,
    matcher: RoadMatcher, require_vlm: bool, default_severity: Severity,
) -> MappingOutcome:
    external_id = _string(document.get("id") or document.get("incidentId") or document.get("_id"))
    if not external_id:
        raise ValueError("VSS incident has no id")
    raw_category = _string(document.get("category") or document.get("type")) or "unknown"
    incident_type, mapping_note = _incident_type(raw_category)
    verdict = _string(_first(document, ("info", "verdict"), ("analyticsModule", "info", "verdict"), ("vlm", "verdict")))
    confirmed = verdict.lower() == "confirmed" if verdict else False
    if incident_type is IncidentType.COLLISION and require_vlm and not confirmed:
        return MappingOutcome(None, filtered_unconfirmed=True)

    place = _string(_first(document, ("place", "name"), ("placeName",)))
    point = _point(_first(document, ("place", "location"), ("location",)))
    sensors = _strings(document.get("sensorId") or document.get("sensorIds"))
    heading = _number(document.get("heading") or document.get("direction"))
    matched = matcher.match(point=point, place=place, sensors=sensors, heading_deg=heading)
    segment = matcher.network.segments.get(matched.segment_id) if matched.segment_id else None
    road = f"{segment.name} {segment.direction}" if segment else None
    description = " · ".join(part for part in (place, road) if part) or "Unmatched VSS incident"
    started = _datetime(document.get("timestamp") or document.get("start"))
    ended = _optional_datetime(document.get("end"))
    severity = _severity(document.get("severity"), default_severity)
    confidence = _number(_first(document, ("analyticsModule", "info", "confidence"), ("info", "confidence")))
    match = IncidentMatch(
        method=matched.method, distance_m=matched.distance_m, confidence=matched.confidence,
        notes=matched.notes, lane_assumed=matched.lane_assumed, mirrored=False, reason=matched.reason,
    )
    incident = Incident(
        id=internal_id,
        external_id=external_id,
        type=incident_type,
        external_category=raw_category,
        type_mapping_note=mapping_note,
        status=IncidentStatus.CLEARED if ended else IncidentStatus.ACTIVE,
        severity=severity,
        location=IncidentLocation(
            segment_id=matched.segment_id, intersection_id=matched.intersection_id,
            position_m=matched.position_m, point=matched.point or point, description=description, match=match,
        ),
        timestamp=started,
        sim_time=sim_time,
        affected_lanes=matched.lanes,
        total_lanes=segment.lanes if segment else None,
        description=_string(document.get("description") or document.get("event")) or f"{raw_category} reported by VSS.",
        source=source,
        sensor_ids=sensors,
        object_ids=_strings(document.get("objectIds") or _first(document, ("info", "primary_object_id"))),
        confidence=confidence,
        vlm_confirmed=confirmed if verdict is not None else None,
        cleared_at=ended,
    )
    return MappingOutcome(incident)


def sensor_records(sensor_payload: Any, place_payload: Any) -> list[SensorRecord]:
    sensors = unpack_collection(sensor_payload, "sensors", "sensorIds")
    places = [item for item in unpack_collection(place_payload, "places") if isinstance(item, dict)]
    place_by_sensor: dict[str, dict[str, Any]] = {}
    for place in places:
        for sensor_id in _strings(place.get("sensorId") or place.get("sensorIds")):
            place_by_sensor[sensor_id] = place
    records: list[SensorRecord] = []
    for item in sensors:
        if isinstance(item, str):
            sensor_id, sensor_doc = item, place_by_sensor.get(item, {})
        elif isinstance(item, dict):
            sensor_id = _string(item.get("sensorId") or item.get("id") or item.get("name")) or ""
            sensor_doc = {**place_by_sensor.get(sensor_id, {}), **item}
        else:
            continue
        if not sensor_id:
            continue
        place = _string(_first(sensor_doc, ("place", "name"), ("name",)))
        point = _point(_first(sensor_doc, ("place", "location"), ("location",)))
        records.append(SensorRecord(sensor_id, place or sensor_id, point, place))
    return records


def external_id(document: dict[str, Any]) -> str | None:
    return _string(document.get("id") or document.get("incidentId") or document.get("_id"))


def _incident_type(category: str) -> tuple[IncidentType, str | None]:
    normalized = category.lower().replace("-", "_").replace(" ", "_")
    exact = {
        "collision": IncidentType.COLLISION, "crash": IncidentType.COLLISION,
        "stalled_vehicle": IncidentType.STALLED_VEHICLE, "vehicle_stalled": IncidentType.STALLED_VEHICLE,
        "wrong_way": IncidentType.WRONG_WAY, "wrongway": IncidentType.WRONG_WAY,
        "congestion": IncidentType.CONGESTION, "traffic_jam": IncidentType.CONGESTION,
    }
    if normalized in exact:
        return exact[normalized], None
    if any(word in normalized for word in ("collision", "crash", "accident")):
        nearest = IncidentType.COLLISION
    elif any(word in normalized for word in ("stall", "disabled", "stopped")):
        nearest = IncidentType.STALLED_VEHICLE
    elif "wrong" in normalized:
        nearest = IncidentType.WRONG_WAY
    else:
        nearest = IncidentType.CONGESTION
    return nearest, f"unknown VSS category '{category}' mapped to {nearest.value}"


def _severity(value: Any, fallback: Severity) -> Severity:
    try:
        return Severity(str(value).lower()) if value is not None else fallback
    except ValueError:
        return fallback


def _datetime(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: Any) -> datetime | None:
    return _datetime(value) if value is not None else None


def _point(value: Any) -> GeoPoint | None:
    if isinstance(value, dict):
        if "coordinates" in value:
            value = value["coordinates"]
        else:
            lat = value["lat"] if "lat" in value else value.get("latitude")
            lon = value["lon"] if "lon" in value else value.get("lng", value.get("longitude"))
            if lat is not None and lon is not None:
                return GeoPoint(lat=float(lat), lon=float(lon))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return GeoPoint(lat=float(value[1]), lon=float(value[0]))
    return None


def _first(value: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = value
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if current is not None:
            return current
    return None


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def _string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
