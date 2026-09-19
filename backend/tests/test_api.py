"""End-to-end API test: real FastAPI app, real live SUMO simulation."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture(scope="module")
def client():
    settings = Settings(sim_warmup_s=120, sim_speed=32, incident_detection_delay_s=2)
    with TestClient(create_app(settings)) as c:
        _wait_for(lambda: c.get("/api/state").status_code == 200)
        yield c


def test_network_and_state(client):
    network = client.get("/api/network").json()
    assert len(network["segments"]) == 48 and len(network["intersections"]) == 9
    state = client.get("/api/state").json()
    assert state["status"] == "running"
    assert state["providers"] == {"smart_city": "mock", "agent": "mock", "simulator": "Eclipse SUMO"}
    assert len(state["vehicles"]) > 0


def test_controls(client):
    try:
        assert client.post("/api/simulation/pause").json()["status"] == "paused"
        time.sleep(0.3)  # let frames already in flight drain
        t = client.get("/api/state").json()["sim_time"]
        time.sleep(0.5)
        assert client.get("/api/state").json()["sim_time"] == t
    finally:
        assert client.post("/api/simulation/start").json()["status"] == "running"
    assert client.post("/api/simulation/speed", json={"multiplier": 16}).json()["speed"] == 16
    assert client.get("/api/signals/B2").json()["intersection_id"] == "B2"
    assert client.get("/api/signals/ZZ").status_code == 404


def test_incident_flow(client):
    assert client.post("/api/emergency/dispatch", json={}).status_code == 409  # nothing to respond to
    response = client.post("/api/incidents/inject", json={"type": "collision"})
    assert response.status_code == 202
    assert response.json()["disruption"]["segment_id"] == "B2_C2"

    incidents = _wait_for(lambda: client.get("/api/incidents").json())
    incident = incidents[0]
    assert incident["type"] == "collision" and incident["location"]["segment_id"] == "B2_C2"

    dispatch = client.post("/api/emergency/dispatch", json={})
    assert dispatch.status_code == 202
    assert dispatch.json()["dispatch"]["destination_segment"] == "B2_C2"

    assert client.post(f"/api/incidents/{incident['id']}/clear").status_code == 200
    _wait_for(lambda: client.get("/api/incidents").json() == [])
    assert client.post("/api/incidents/nope/clear").status_code == 404


def test_websocket_streams_state(client):
    with client.websocket_connect("/ws/state") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["data"]["status"]["status"] in ("running", "paused")
        kinds = set()
        for _ in range(20):
            kinds.add(json.loads(ws.receive_text())["type"])
            if "state" in kinds:
                break
        assert "state" in kinds
