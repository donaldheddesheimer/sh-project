"""Mock Smart City provider backed by simulation ground truth.

It stands in for NVIDIA VSS: it watches the disruptions the simulation is
modelling and reports each as an Incident after a detection delay, the way
video analytics would report a crash seen on camera a few seconds after it
happens. When the disruption disappears (cleared), the incident is closed.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from app.models.domain import Disruption, Incident, IncidentLocation, IncidentStatus, NetworkState, Severity
from app.simulation.network import RoadNetwork
from app.smart_city.base import Camera, EventSink, SmartCityEvent, SmartCityEventKind, SmartCityProvider

LANE_NAMES = {0: "right lane", 1: "left lane"}


class MockSmartCityProvider(SmartCityProvider):
    name = "mock"

    def __init__(self, network: RoadNetwork, detection_delay_s: float = 4.0):
        self._network = network
        self._delay = detection_delay_s
        self._emit: EventSink | None = None
        self._incidents: dict[str, Incident] = {}
        self._by_disruption: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._cameras = [
            Camera(
                id=f"CAM-{info.id}",
                name=info.name,
                location=network.projector.to_point(info.x, info.y),
                intersection_id=info.id,
            )
            for info in network.intersections.values()
        ]

    async def start(self, emit: EventSink) -> None:
        self._emit = emit

    async def stop(self) -> None:
        self._emit = None

    async def observe(self, state: NetworkState) -> None:
        """Feed one simulation frame (wired up by the provider factory, not by consumers)."""
        active = {d.id: d for d in state.disruptions}
        # Clear first: after a reset the first frame drops the old crashes and may already carry a new one, and
        # whoever reacts to the detection must not see the old incidents as still active.
        for disruption_id, incident_id in list(self._by_disruption.items()):
            incident = self._incidents[incident_id]
            if disruption_id not in active and incident.status is IncidentStatus.ACTIVE:
                incident.status = IncidentStatus.CLEARED
                incident.cleared_at = datetime.now(UTC)
                await self._publish(SmartCityEventKind.INCIDENT_CLEARED, incident)

        for disruption in active.values():
            if disruption.id in self._by_disruption or state.sim_time - disruption.started_at < self._delay:
                continue
            incident = self._incident_from(disruption, state.sim_time)
            self._incidents[incident.id] = incident
            self._by_disruption[disruption.id] = incident.id
            await self._publish(SmartCityEventKind.INCIDENT_DETECTED, incident)

    async def list_incidents(self, include_cleared: bool = False) -> list[Incident]:
        return [i for i in self._incidents.values() if include_cleared or i.status is IncidentStatus.ACTIVE]

    async def get_incident(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    async def list_cameras(self) -> list[Camera]:
        return list(self._cameras)

    # ----------------------------------------------------------------- helpers

    async def _publish(self, kind: SmartCityEventKind, incident: Incident) -> None:
        if self._emit is not None:
            await self._emit(SmartCityEvent(kind=kind, incident=incident.model_copy()))

    def _incident_from(self, d: Disruption, sim_time: float) -> Incident:
        seg = self._network.segments[d.segment_id]
        upstream = self._network.intersections.get(seg.source)
        downstream = self._network.intersections.get(seg.destination)
        between = " and ".join(
            _cross_street(i.name, seg.name) for i in (upstream, downstream) if i is not None
        )
        road = f"{seg.name} {seg.direction}" + (f" between {between}" if between else "")
        if len(d.lanes) == d.total_lanes:
            blocked = "all lanes"
        else:
            blocked = ", ".join(LANE_NAMES.get(lane, f"lane {lane + 1}") for lane in d.lanes)
        n_vehicles = len(d.vehicle_ids)
        nearest = downstream or upstream
        return Incident(
            id=f"INC-{next(self._ids):04d}",
            type=d.kind,
            severity=d.severity,
            location=IncidentLocation(
                segment_id=d.segment_id,
                intersection_id=nearest.id if nearest else None,
                position_m=d.position_m,
                point=d.point,
                description=road,
            ),
            timestamp=datetime.now(UTC),
            sim_time=sim_time,
            affected_lanes=d.lanes,
            total_lanes=d.total_lanes,
            description=f"{n_vehicles}-vehicle collision blocking {blocked} on {road}."
            + (" Traffic passing the scene at walking pace." if d.severity is Severity.MAJOR and d.pass_speed else ""),
            source=self.name,
            sensor_ids=[f"CAM-{i.id}" for i in (downstream, upstream) if i is not None],
            object_ids=list(d.vehicle_ids),
        )


def _cross_street(intersection_name: str, street: str) -> str:
    """'Central Ave & Main St' seen from Main St -> 'Central Ave'."""
    parts = [p for p in intersection_name.split(" & ") if p != street]
    return parts[0] if parts else intersection_name
