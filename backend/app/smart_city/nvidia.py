"""NVIDIA Video Analytics MCP-backed Smart City provider.

The adapter follows NVIDIA's VSS 3.2 Video Analytics MCP reference. Current tool names are
``video_analytics__get_incidents``, ``video_analytics__get_incident``,
``video_analytics__get_sensor_ids`` and ``video_analytics__get_places`` (the client also accepts
the older unprefixed names). The reference documents incident fields ``id``, ``sensorId``,
``timestamp``, ``end``, ``category``, ``place.name``, ``info.verdict`` and optional
``objectIds``/``info``. Compatibility with older ``start`` and nested analytics-module fields
is isolated in ``vss_mapping.py``. No live endpoint has been exercised by this repository.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import UTC, datetime

from app.models.domain import Incident, IncidentStatus, NetworkState, Severity
from app.smart_city.base import Camera, EventSink, SmartCityEvent, SmartCityEventKind, SmartCityProvider, SmartCityStatus
from app.smart_city.matching import RoadMatcher
from app.smart_city.vss_client import VssClient
from app.smart_city.vss_mapping import external_id, map_vss_document, sensor_records

log = logging.getLogger(__name__)


class NvidiaSmartCityProvider(SmartCityProvider):
    simulation_is_source = False

    def __init__(
        self,
        client: VssClient,
        matcher: RoadMatcher,
        poll_s: float,
        require_vlm_confirmation: bool,
        default_severity: Severity,
    ):
        self.name = client.name
        self._client = client
        self._matcher = matcher
        self._poll_s = poll_s
        self._require_vlm = require_vlm_confirmation
        self._default_severity = default_severity
        self._emit: EventSink | None = None
        self._task: asyncio.Task[None] | None = None
        self._incidents: dict[str, Incident] = {}
        self._by_external: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._cameras: list[Camera] = []
        self._sim_time = 0.0
        self._last_success: datetime | None = None
        self._last_error: str | None = None
        self._filtered: set[str] = set()
        self._malformed: set[str] = set()
        self._down = False

    async def start(self, emit: EventSink) -> None:
        self._emit = emit
        self._task = asyncio.create_task(self._poll_loop(), name="vss-poller")

    async def stop(self) -> None:
        self._emit = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def observe(self, state: NetworkState) -> None:
        self._sim_time = state.sim_time
        self._client.set_sim_time(state.sim_time)

    async def list_incidents(self, include_cleared: bool = False) -> list[Incident]:
        return [
            incident.model_copy(deep=True)
            for incident in self._incidents.values()
            if include_cleared or incident.status is IncidentStatus.ACTIVE
        ]

    async def get_incident(self, incident_id: str) -> Incident | None:
        incident = self._incidents.get(incident_id)
        return incident.model_copy(deep=True) if incident else None

    async def list_cameras(self) -> list[Camera]:
        return [camera.model_copy(deep=True) for camera in self._cameras]

    def status(self) -> SmartCityStatus:
        return SmartCityStatus(
            ok=not self._down,
            last_success=self._last_success,
            last_error=self._last_error,
            filtered_unconfirmed=len(self._filtered),
            malformed_documents=len(self._malformed),
        )

    def set_mirrored(self, incident_id: str, mirrored: bool) -> None:
        incident = self._incidents.get(incident_id)
        if incident and incident.location.match:
            incident.location.match.mirrored = mirrored

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            try:
                await self._poll()
                if self._down:
                    log.info("VSS endpoint recovered")
                self._down = False
                self._last_error = None
                self._last_success = datetime.now(UTC)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # endpoint failure must never stop the twin
                failures += 1
                self._last_error = str(exc)
                if not self._down:
                    log.warning("VSS endpoint unavailable: %s", exc)
                self._down = True
            delay = min(60.0, self._poll_s * (2 ** min(failures, 4))) if failures else self._poll_s
            await asyncio.sleep(delay)

    async def _poll(self) -> None:
        active = [incident.timestamp for incident in self._incidents.values() if incident.status is IncidentStatus.ACTIVE]
        documents = await self._client.incidents_since(min(active) if active else self._last_success)
        if not self._cameras:
            await self._load_cameras()
        mapped: list[tuple[str, Incident]] = []
        for index, document in enumerate(documents):
            ext_id = external_id(document)
            marker = ext_id or f"document-{index}"
            try:
                internal_id = self._by_external.get(ext_id or "")
                previous = self._incidents.get(internal_id) if internal_id else None
                outcome = map_vss_document(
                    document,
                    internal_id=internal_id or "INC-PENDING",
                    source=self.name,
                    sim_time=previous.sim_time if previous and previous.sim_time is not None else self._sim_time,
                    matcher=self._matcher,
                    require_vlm=self._require_vlm,
                    default_severity=self._default_severity,
                )
                if outcome.filtered_unconfirmed:
                    self._filtered.add(marker)
                    continue
                incident = outcome.incident
                if incident is None or ext_id is None:
                    continue
                mapped.append((ext_id, incident))
            except Exception as exc:
                if marker not in self._malformed:
                    log.warning("Ignoring malformed VSS incident %s: %s", marker, exc)
                self._malformed.add(marker)
                raise RuntimeError(f"malformed VSS incident {marker}: {exc}") from exc

        for ext_id, incident in mapped:
            internal_id = self._by_external.get(ext_id)
            previous = self._incidents.get(internal_id) if internal_id else None
            if internal_id is None:
                internal_id = f"INC-{next(self._ids):04d}"
                incident.id = internal_id
            if previous and previous.location.match and incident.location.match:
                incident.location.match.mirrored = previous.location.match.mirrored
            self._by_external[ext_id] = internal_id
            self._incidents[internal_id] = incident
            if previous is None:
                await self._publish(SmartCityEventKind.INCIDENT_DETECTED, incident)
            elif incident.status is IncidentStatus.CLEARED and previous.status is IncidentStatus.ACTIVE:
                await self._publish(SmartCityEventKind.INCIDENT_CLEARED, incident)
            elif incident.model_dump() != previous.model_dump():
                await self._publish(SmartCityEventKind.INCIDENT_UPDATED, incident)

    async def _load_cameras(self) -> None:
        records = sensor_records(await self._client.get_sensors(), await self._client.get_places())
        cameras: list[Camera] = []
        for record in records:
            matched = self._matcher.match(point=record.point, place=record.place)
            self._matcher.register_sensor(record.id, matched.intersection_id)
            cameras.append(
                Camera(
                    id=record.id,
                    name=record.name,
                    location=record.point or matched.point,
                    intersection_id=matched.intersection_id,
                )
            )
        self._cameras = cameras

    async def _publish(self, kind: SmartCityEventKind, incident: Incident) -> None:
        if self._emit is not None:
            await self._emit(SmartCityEvent(kind=kind, incident=incident.model_copy(deep=True)))
