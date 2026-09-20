"""Provider selection (SMART_CITY_PROVIDER / AGENT_PROVIDER / EPISODE_ANALYST) and service assembly."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from mcp.server.mcpserver import MCPServer

from app.agent.base import AgentProvider
from app.agent.claude import ClaudeClient
from app.agent.mock import MockAgentProvider
from app.agent.nemotron import NemotronAgentProvider, NimClient
from app.config import Settings
from app.learning.embeddings import NimEmbedder
from app.learning.analysts import MockAnalyst, ModelAnalyst
from app.learning.episode import AgentTeam, EpisodeService
from app.learning.implementor import Implementor
from app.learning.monitor import LiveMonitor
from app.learning.reviewer import MockReviewer, ModelReviewer
from app.learning.store import ExperienceStore
from app.models.domain import Severity
from app.models.api import EventLevel
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
    if not settings.nemotron_model:
        raise RuntimeError("AGENT_PROVIDER=nemotron requires NEMOTRON_MODEL (a NIM model id)")
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    return NemotronAgentProvider(
        settings.nemotron_base_url, settings.nemotron_model, api_key, settings.scenario_max_candidates
    )


def build_episode_teams(
    settings: Settings, scenarios: ScenarioService, implementor: Implementor, mcp_server: MCPServer
) -> tuple[dict[str, AgentTeam], str]:
    """Configured analyst/reviewer teams and the startup selection.

    ``EPISODE_ANALYST`` is only the startup default; the operator may switch teams between episodes.
    ``auto`` prefers Claude, then Nemotron, then the mock so configured NVIDIA credits are not spent by surprise.
    """
    mock = MockAnalyst(scenarios, implementor, settings.agent_may_implement)
    mock_reviewer = MockReviewer()
    teams = {"mock": AgentTeam(mock, None, mock_reviewer)}
    fallback_analyst = mock if settings.episode_fallback_to_mock else None
    fallback_reviewer = mock_reviewer if settings.episode_fallback_to_mock else None

    claude_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    if claude_key and settings.claude_model:
        claude = ClaudeClient(
            settings.claude_base_url,
            settings.claude_model,
            claude_key,
            settings.anthropic_workspace_id,
        )
        teams["claude"] = AgentTeam(
            ModelAnalyst(
                "claude",
                claude,
                settings.mcp_url or mcp_server,
                settings.episode_agent_timeout_s,
                settings.agent_may_implement,
            ),
            fallback_analyst,
            ModelReviewer("claude", claude, fallback_reviewer),
            settings.claude_model,
        )

    nvidia_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    if nvidia_key and settings.nemotron_model:
        nim = NimClient(settings.nemotron_base_url, settings.nemotron_model, nvidia_key)
        teams["nemotron"] = AgentTeam(
            ModelAnalyst(
                "nemotron",
                nim,
                settings.mcp_url or mcp_server,
                settings.episode_agent_timeout_s,
                settings.agent_may_implement,
            ),
            fallback_analyst,
            ModelReviewer("nemotron", nim, fallback_reviewer),
            settings.nemotron_model,
        )

    wanted = settings.episode_analyst
    if wanted == "auto":
        selected = "claude" if "claude" in teams else "nemotron" if "nemotron" in teams else "mock"
    else:
        selected = wanted
    if selected not in teams:
        needed = (
            "ANTHROPIC_API_KEY and CLAUDE_MODEL"
            if selected == "claude"
            else "NVIDIA_API_KEY and NEMOTRON_MODEL"
        )
        raise RuntimeError(f"EPISODE_ANALYST={selected} requires {needed}")
    return teams, selected


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
            # the live city degrades rather than freezing until a Reset; branches keep failing loudly
            fail_safe_preemption=True,
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
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    embedder = (
        NimEmbedder(settings.embedding_base_url or settings.nemotron_base_url, settings.embedding_model, api_key)
        if settings.embedding_model
        else None
    )

    def report_embedding_event(message: str, level: EventLevel) -> None:
        city.events.add(level, message, city.sim_time)

    memory = ExperienceStore(
        settings.memory_dir,
        enabled=settings.memory_enabled,
        embedder=embedder,
        on_embedding_event=report_embedding_event,
    )
    teams, selected_team = build_episode_teams(settings, scenarios, implementor, mcp_server)
    episodes = EpisodeService(
        settings=settings,
        city=city,
        scenarios=scenarios,
        implementor=implementor,
        monitor=LiveMonitor(),
        store=memory,
        teams=teams,
        selected_team=selected_team,
    )
    return Services(city=city, scenarios=scenarios, implementor=implementor, episodes=episodes, memory=memory)
