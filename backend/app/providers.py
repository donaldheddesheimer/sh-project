"""Provider selection (SMART_CITY_PROVIDER / AGENT_PROVIDER) and service assembly."""

from __future__ import annotations

from itertools import count

from app.agent.base import AgentProvider
from app.agent.mock import MockAgentProvider
from app.agent.nemotron import NemotronAgentProvider
from app.config import Settings
from app.services.city import CityService, FrameObserver
from app.simulation.network import RoadNetwork
from app.simulation.scenario import load_scenario
from app.simulation.sumo import SumoSimulation
from app.smart_city.base import SmartCityProvider
from app.smart_city.mock import MockSmartCityProvider
from app.smart_city.nvidia import NvidiaSmartCityProvider
from app.websocket.hub import ConnectionHub


def build_smart_city_provider(settings: Settings, network: RoadNetwork) -> tuple[SmartCityProvider, list[FrameObserver]]:
    """Returns the provider plus any simulation-frame observers it needs.

    Only the mock needs frames (it detects incidents from simulation ground
    truth); the NVIDIA provider gets its data from VSS.
    """
    if settings.smart_city_provider == "mock":
        provider = MockSmartCityProvider(network, detection_delay_s=settings.incident_detection_delay_s)
        return provider, [provider.observe]
    if not settings.nvidia_va_mcp_url:
        raise RuntimeError("SMART_CITY_PROVIDER=nvidia requires NVIDIA_VA_MCP_URL")
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    return NvidiaSmartCityProvider(settings.nvidia_va_mcp_url, api_key), []


def build_agent_provider(settings: Settings) -> AgentProvider:
    if settings.agent_provider == "mock":
        return MockAgentProvider()
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    return NemotronAgentProvider(settings.nemotron_base_url, settings.nemotron_model, api_key)


def build_city_service(settings: Settings, hub: ConnectionHub) -> CityService:
    scenario = load_scenario(settings.scenario_dir)
    network = RoadNetwork(scenario)
    instance = count(1)

    def live_simulation() -> SumoSimulation:
        return SumoSimulation(
            scenario,
            network,
            label=f"live-{next(instance)}",
            snapshot_dir=settings.snapshot_dir,
            sumo_binary=settings.sumo_binary,
            gui=settings.sumo_gui,
        )

    smart_city, observers = build_smart_city_provider(settings, network)
    return CityService(
        settings=settings,
        scenario=scenario,
        network=network,
        sim_factory=live_simulation,
        smart_city=smart_city,
        agent=build_agent_provider(settings),
        frame_observers=observers,
        hub=hub,
    )
