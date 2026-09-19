"""Adapter boundary for NVIDIA Smart City / VSS (not implemented yet).

Target integration (from the VSS blueprint's `smartcities` profile):
the Video Analytics MCP server (vss-agent `va_mcp_server_config.yml`) exposes
tools backed by Elasticsearch indices `mdx-*`:

    get_incidents / get_incident   -> mdx-incidents-* (+ mdx-vlm-incidents-* verdicts)
    get_sensor_ids / get_places    -> camera + intersection registry (calibration.json)
    get_average_speeds             -> per-sensor speed metrics
    get_fov_histogram              -> object counts in a camera's field of view

Planned field mapping from a VSS incident document to our Incident:

    id                               -> external id (keep VSS id for traceability)
    category / type                  -> IncidentType (collision, stalled_vehicle, wrong_way, ...)
    start / end                      -> timestamp / cleared_at
    sensorId                         -> sensor_ids
    place.name, place.location       -> location.description, location.point
    objectIds, info.primary_object_id -> object_ids
    analyticsModule.info.confidence  -> confidence
    mdx-vlm-incidents verdict        -> only surface VLM-confirmed collisions

Location needs map-matching: VSS reports lat/lon + intersection names, while
the simulation needs a segment id, lane and position. That belongs in a
dedicated matcher (nearest segment by geometry + heading from `direction`),
not in the UI.
"""

from __future__ import annotations

from app.models.domain import Incident
from app.smart_city.base import Camera, EventSink, SmartCityProvider

_NOT_READY = (
    "NvidiaSmartCityProvider is a placeholder: the VSS Video Analytics MCP client is not implemented yet. "
    "Use SMART_CITY_PROVIDER=mock."
)


class NvidiaSmartCityProvider(SmartCityProvider):
    name = "nvidia-vss"

    def __init__(self, mcp_url: str, api_key: str | None = None):
        self._mcp_url = mcp_url
        self._api_key = api_key

    async def start(self, emit: EventSink) -> None:
        raise NotImplementedError(_NOT_READY)

    async def stop(self) -> None:
        return None

    async def list_incidents(self, include_cleared: bool = False) -> list[Incident]:
        raise NotImplementedError(_NOT_READY)

    async def get_incident(self, incident_id: str) -> Incident | None:
        raise NotImplementedError(_NOT_READY)

    async def list_cameras(self) -> list[Camera]:
        raise NotImplementedError(_NOT_READY)
