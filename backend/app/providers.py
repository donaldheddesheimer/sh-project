"""Provider selection and service assembly; model teams are selected at runtime in the console."""

from __future__ import annotations

import logging
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

log = logging.getLogger(__name__)


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
        raise RuntimeError("demo scripts require the mock Smart City provider")
    if not settings.vss_replay_file and not settings.nvidia_va_mcp_url:
        raise RuntimeError("the NVIDIA Smart City provider requires a live MCP client or replay source")
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
        raise RuntimeError("the Nemotron REST provider requires a model id")
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    return NemotronAgentProvider(
        settings.nemotron_base_url, settings.nemotron_model, api_key, settings.scenario_max_candidates
    )


def build_episode_teams(
    settings: Settings, scenarios: ScenarioService, implementor: Implementor, mcp_server: MCPServer
) -> tuple[dict[str, AgentTeam], str]:
    """Configured analyst/reviewer teams and the startup selection.

    The console can choose among the configured teams; Mock is always available for local development.
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
        needed = "an Anthropic API key" if selected == "claude" else "an NVIDIA API key"
        raise RuntimeError(f"analyst {selected} requires {needed}")
    log.info("episode analyst/reviewer team: %s (available: %s)", selected, ", ".join(sorted(teams)))
    return teams, selected


# traci registers labelled connections in process-global state, so these counters outlive any one service
# graph. A map switch overlaps the old and new live simulations, and a reused label is rejected outright
# ("Connection 'live-1' already active"), which would make every runtime switch fail.
_live_labels = count(1)
_branch_labels = count(1)


def build_services(settings: Settings, hub: ConnectionHub, mcp_server: MCPServer) -> Services:
    scenario = load_scenario(settings.scenario_dir)
    network = RoadNetwork(scenario)

    def live_simulation() -> SumoSimulation:
        return SumoSimulation(
            scenario,
            network,
            label=f"live-{next(_live_labels)}",
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
            label=f"branch-{next(_branch_labels)}",
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
