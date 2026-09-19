"""Provider selection (SMART_CITY_PROVIDER / AGENT_PROVIDER / EPISODE_ANALYST) and service assembly."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from mcp.server.mcpserver import MCPServer

from app.agent.base import AgentProvider
from app.agent.mock import MockAgentProvider
from app.agent.nemotron import NemotronAgentProvider, NimClient
from app.config import Settings
from app.learning.analysts import MockAnalyst, NemotronAnalyst
from app.learning.episode import Analyst, EpisodeService, Reviewer
from app.learning.implementor import Implementor
from app.learning.monitor import LiveMonitor
from app.learning.reviewer import MockReviewer, NemotronReviewer
from app.learning.store import ExperienceStore
from app.models.domain import Severity
from app.safety.validator import RuleBasedSafetyValidator
from app.services.city import CityService, FrameObserver
from app.services.scenarios import ScenarioService
from app.simulation.network import RoadNetwork
from app.simulation.scenario import load_scenario
from app.simulation.sumo import SumoSimulation
from app.smart_city.base import SmartCityProvider
from app.smart_city.mock import MockSmartCityProvider
from app.smart_city.matching import RoadMatcher
from app.smart_city.nvidia import NvidiaSmartCityProvider
from app.smart_city.vss_client import McpVssClient, ReplayVssClient
from app.websocket.hub import ConnectionHub


@dataclass
class Services:
    city: CityService
    scenarios: ScenarioService
    implementor: Implementor
    episodes: EpisodeService
    memory: ExperienceStore


def build_smart_city_provider(settings: Settings, network: RoadNetwork) -> tuple[SmartCityProvider, list[FrameObserver]]:
    """Returns the provider plus any simulation-frame observers it needs.

    Both providers observe frames: the mock sees simulation truth, while VSS replay uses
    simulation time so its timeline is reproducible at every live speed.
    """
    if settings.smart_city_provider == "mock":
        provider = MockSmartCityProvider(network, detection_delay_s=settings.incident_detection_delay_s)
        return provider, [provider.observe]
    if settings.demo_script:
        raise RuntimeError("DEMO_SCRIPT cannot be used with SMART_CITY_PROVIDER=nvidia; use the mock provider")
    if not settings.vss_replay_file and not settings.nvidia_va_mcp_url:
        raise RuntimeError("SMART_CITY_PROVIDER=nvidia requires NVIDIA_VA_MCP_URL or VSS_REPLAY_FILE")
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    client = (
        ReplayVssClient(settings.vss_replay_file)
        if settings.vss_replay_file
        else McpVssClient(settings.nvidia_va_mcp_url or "", api_key)
    )
    provider = NvidiaSmartCityProvider(
        client,
        RoadMatcher(network, settings.vss_match_max_dist_m),
        settings.vss_poll_s,
        settings.vss_require_vlm_confirmation,
        Severity(settings.vss_default_severity),
    )
    return provider, [provider.observe]


def build_agent_provider(settings: Settings) -> AgentProvider:
    if settings.agent_provider == "mock":
        return MockAgentProvider()
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    return NemotronAgentProvider(settings.nemotron_base_url, settings.nemotron_model, api_key)


def build_episode_agents(
    settings: Settings, scenarios: ScenarioService, implementor: Implementor, mcp_server: MCPServer
) -> tuple[Analyst, Analyst | None, Reviewer]:
    """The analyst new episodes use, its fallback, and the reviewer.

    EPISODE_ANALYST=auto picks Nemotron when NVIDIA_API_KEY and NEMOTRON_MODEL are set, the mock otherwise.
    """
    mock = MockAnalyst(scenarios, implementor, settings.agent_may_implement)
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    wanted = settings.episode_analyst
    if wanted == "mock" or (wanted == "auto" and not (api_key and settings.nemotron_model)):
        return mock, None, MockReviewer()
    if not settings.nemotron_model:
        raise RuntimeError("EPISODE_ANALYST=nemotron requires NEMOTRON_MODEL (a NIM model id)")
    nim = NimClient(settings.nemotron_base_url, settings.nemotron_model, api_key)
    analyst = NemotronAnalyst(
        nim, settings.mcp_url or mcp_server, settings.episode_agent_timeout_s, settings.agent_may_implement
    )
    return analyst, (mock if settings.episode_fallback_to_mock else None), NemotronReviewer(nim, MockReviewer())


def build_services(settings: Settings, hub: ConnectionHub, mcp_server: MCPServer) -> Services:
    scenario = load_scenario(settings.scenario_dir)
    network = RoadNetwork(scenario)
    instance = count(1)
    branch = count(1)

    def live_simulation() -> SumoSimulation:
        return SumoSimulation(
            scenario,
            network,
            label=f"live-{next(instance)}",
            snapshot_dir=settings.snapshot_dir,
            sumo_binary=settings.sumo_binary,
            gui=settings.sumo_gui,
        )

    def branch_simulation() -> SumoSimulation:
        # a brand-new SUMO process per candidate branch (never reused; see branching.py)
        return SumoSimulation(
            scenario,
            network,
            label=f"branch-{next(branch)}",
            snapshot_dir=settings.snapshot_dir,
            sumo_binary=settings.sumo_binary,
        )

    smart_city, observers = build_smart_city_provider(settings, network)
    agent = build_agent_provider(settings)
    city = CityService(
        settings=settings,
        scenario=scenario,
        network=network,
        sim_factory=live_simulation,
        smart_city=smart_city,
        agent=agent,
        frame_observers=observers,
        hub=hub,
    )
    validator = RuleBasedSafetyValidator()
    scenarios = ScenarioService(
        settings=settings,
        city=city,
        agent=agent,
        validator=validator,
        branch_factory=branch_simulation,
    )
    implementor = Implementor(city=city, scenarios=scenarios, validator=validator)
    memory = ExperienceStore(settings.memory_dir, enabled=settings.memory_enabled)
    analyst, fallback, reviewer = build_episode_agents(settings, scenarios, implementor, mcp_server)
    episodes = EpisodeService(
        settings=settings,
        city=city,
        scenarios=scenarios,
        implementor=implementor,
        monitor=LiveMonitor(),
        store=memory,
        analyst=analyst,
        fallback=fallback,
        reviewer=reviewer,
    )
    return Services(city=city, scenarios=scenarios, implementor=implementor, episodes=episodes, memory=memory)
