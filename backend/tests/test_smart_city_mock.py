import asyncio

from app.models.domain import (
    Disruption,
    GeoPoint,
    IncidentStatus,
    IncidentType,
    NetworkState,
    Severity,
    TrafficMetrics,
)
from app.smart_city.base import SmartCityEventKind
from app.smart_city.mock import MockSmartCityProvider


def _frame(sim_time: float, disruptions: list[Disruption]) -> NetworkState:
    return NetworkState(
        sim_time=sim_time,
        intersections=[],
        segments=[],
        vehicles=[],
        emergency_vehicles=[],
        metrics=TrafficMetrics(sim_time=sim_time, mean_vehicle_delay=0, max_queue_length=0, throughput=0, mean_speed=0),
        disruptions=disruptions,
    )


def test_detects_after_delay_and_clears(network):
    crash = Disruption(
        id="C-1",
        kind=IncidentType.COLLISION,
        segment_id="B2_C2",
        lanes=[0],
        total_lanes=2,
        position_m=120,
        severity=Severity.MAJOR,
        started_at=100,
        point=GeoPoint(lat=0, lon=0),
        vehicle_ids=["C-1-00", "C-1-01"],
        pass_speed=0.6,
    )
    provider = MockSmartCityProvider(network, detection_delay_s=4)
    events = []

    async def scenario():
        async def sink(event):
            events.append(event)

        await provider.start(sink)
        await provider.observe(_frame(102, [crash]))
        assert events == []  # not yet visible to the (simulated) cameras
        await provider.observe(_frame(104, [crash]))
        await provider.observe(_frame(110, [crash]))  # reported once, not every frame
        active = await provider.list_incidents()
        await provider.observe(_frame(120, []))
        return active, await provider.list_incidents(), await provider.list_incidents(include_cleared=True)

    active, after_clear, history = asyncio.run(scenario())
    assert [e.kind for e in events] == [SmartCityEventKind.INCIDENT_DETECTED, SmartCityEventKind.INCIDENT_CLEARED]
    incident = active[0]
    assert incident.location.description == "Main St EB between Central Ave and Pine Ave"
    assert incident.sensor_ids == ["CAM-C2", "CAM-B2"]
    assert incident.affected_lanes == [0] and incident.total_lanes == 2
    assert after_clear == []
    assert history[0].status is IncidentStatus.CLEARED
