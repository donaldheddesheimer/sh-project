"""REST API. Every operation here is also a candidate MCP tool later."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket

from app.models.api import (
    CityState,
    ControlResponse,
    DispatchRequest,
    DispatchResponse,
    InjectIncidentRequest,
    InjectIncidentResponse,
    OpsEvent,
    SpeedRequest,
)
from app.models.domain import Incident, NetworkGeometry, SignalProgram
from app.models.scenario import ScenarioRun, ScenarioRunRequest
from app.services.city import CityService, Conflict, NotReady
from app.services.scenarios import ScenarioService
from app.smart_city.base import Camera

router = APIRouter(prefix="/api")
ws_router = APIRouter()


def get_city(request: Request) -> CityService:
    return request.app.state.city


def get_scenarios(request: Request) -> ScenarioService:
    return request.app.state.scenarios


City = Annotated[CityService, Depends(get_city)]
Scenarios = Annotated[ScenarioService, Depends(get_scenarios)]


def _control(city: CityService) -> ControlResponse:
    status = city.status
    return ControlResponse(status=status.status, speed=status.speed, sim_time=city.sim_time)


@router.get("/health")
async def health(city: City) -> dict:
    return {"ok": True, "status": city.status.status, "providers": city.providers, "clients": city.hub.client_count}


@router.get("/network", response_model=NetworkGeometry)
async def network(city: City) -> NetworkGeometry:
    return city.geometry


@router.get("/state", response_model=CityState)
async def state(city: City) -> CityState:
    try:
        return city.state
    except NotReady as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/incidents", response_model=list[Incident])
async def incidents(city: City, include_cleared: bool = False) -> list[Incident]:
    return await city.incidents(include_cleared=include_cleared)


@router.get("/incidents/{incident_id}", response_model=Incident)
async def incident(city: City, incident_id: str) -> Incident:
    found = await city.smart_city.get_incident(incident_id)
    if found is None:
        raise HTTPException(404, f"unknown incident {incident_id}")
    return found


@router.post("/incidents/inject", response_model=InjectIncidentResponse, status_code=202)
async def inject_incident(city: City, request: InjectIncidentRequest) -> InjectIncidentResponse:
    try:
        disruption = await city.inject_incident(request)
    except KeyError as exc:
        raise HTTPException(404, f"unknown segment {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return InjectIncidentResponse(
        disruption=disruption,
        message=f"Collision staged on {disruption.segment_id}; the Smart City provider will report it once detected.",
    )


@router.post("/incidents/{incident_id}/clear")
async def clear_incident(city: City, incident_id: str) -> dict:
    try:
        cleared = await city.clear_incident(incident_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown incident {incident_id}") from exc
    return {"incident_id": incident_id, "cleared_disruptions": cleared}


@router.post("/simulation/start", response_model=ControlResponse)
async def start(city: City) -> ControlResponse:
    await city.set_running(True)
    return _control(city)


@router.post("/simulation/pause", response_model=ControlResponse)
async def pause(city: City) -> ControlResponse:
    await city.set_running(False)
    return _control(city)


@router.post("/simulation/reset", response_model=ControlResponse)
async def reset(city: City) -> ControlResponse:
    await city.reset()
    return _control(city)


@router.post("/simulation/speed", response_model=ControlResponse)
async def speed(city: City, request: SpeedRequest) -> ControlResponse:
    await city.set_speed(request.multiplier)
    return _control(city)


@router.post("/emergency/dispatch", response_model=DispatchResponse, status_code=202)
async def dispatch(city: City, request: DispatchRequest) -> DispatchResponse:
    try:
        result = await city.dispatch_emergency(request.origin_segment, request.destination_segment)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, f"unknown segment {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DispatchResponse(dispatch=result)


@router.get("/signals/{intersection_id}", response_model=SignalProgram)
async def signal_program(city: City, intersection_id: str) -> SignalProgram:
    try:
        return await city.signal_program(intersection_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, f"no signal program for {intersection_id}") from exc


@router.post("/scenarios/run", response_model=ScenarioRun, status_code=202)
async def run_scenario(scenarios: Scenarios, request: ScenarioRunRequest) -> ScenarioRun:
    try:
        return await scenarios.start_run(request)
    except NotReady as exc:
        raise HTTPException(503, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, f"unknown incident {exc.args[0]}") from exc


@router.get("/scenarios", response_model=list[ScenarioRun])
async def list_scenarios(scenarios: Scenarios) -> list[ScenarioRun]:
    return scenarios.runs()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioRun)
async def scenario(scenarios: Scenarios, scenario_id: str) -> ScenarioRun:
    try:
        return scenarios.get(scenario_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown scenario run {scenario_id}") from exc


@router.get("/cameras", response_model=list[Camera])
async def cameras(city: City) -> list[Camera]:
    return await city.smart_city.list_cameras()


@router.get("/events", response_model=list[OpsEvent])
async def events(city: City, limit: int = 50) -> list[OpsEvent]:
    return city.events.recent(limit)


@ws_router.websocket("/ws/state")
async def state_stream(ws: WebSocket) -> None:
    city: CityService = ws.app.state.city
    await city.hub.serve(ws, hello=city.hello_message())
