"""Provider selection and service assembly; model teams are selected at runtime in the console."""

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
from app.learning.analysts import MockAnalyst, ModelAnalyst, PipelineAnalyst
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


def check_keyed_deployment(settings: Settings) -> None:
    """A deployment holding an NVIDIA key must name its analyst and reviewer models.

    Both the REST agent and the episode teams depend on this, and whichever is built first
    would otherwise report its own narrower complaint: `build_agent_provider` raises about a
    missing model id, which reads like a typo rather than a deployment that cannot serve what
    its key promises. Checked once, up front, so the message is the accurate one.
    """
    if settings.nvidia_api_key and settings.nvidia_api_key.get_secret_value() and (
        not settings.nemotron_model or not settings.nemotron_reviewer_model
    ):
        raise RuntimeError(
            "NVIDIA_API_KEY is set but a Nemotron analyst or reviewer model is empty. A deployment holding "
            "a key must configure both model roles, or drop the key to run locally."
        )


def build_agent_provider(settings: Settings) -> AgentProvider:
    api_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    selected = settings.agent_provider
    if selected == "auto":
        selected = "nemotron" if api_key else "mock"
    if selected == "mock":
        return MockAgentProvider()
    if not settings.nemotron_model:
        raise RuntimeError("the Nemotron REST provider requires a model id")
    if not api_key:
        raise RuntimeError("the Nemotron REST provider requires an NVIDIA API key")
    return NemotronAgentProvider(
        settings.nemotron_base_url,
        settings.nemotron_model,
        api_key,
        settings.scenario_max_candidates,
        settings.nemotron_timeout_s,
    )


def build_episode_teams(
    settings: Settings, scenarios: ScenarioService, implementor: Implementor, mcp_server: MCPServer
) -> tuple[dict[str, AgentTeam], str]:
    """Configured analyst/reviewer teams and the startup selection.

    A configured NVIDIA key makes the backend Nemotron-first and removes the deterministic
    local team from its advertised choices. This keeps the deployed backend model-backed.
    """
    claude_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    nvidia_key = settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
    teams: dict[str, AgentTeam] = {}
    fallback_analyst = None
    fallback_reviewer = None

    # The key is what suppresses the local team, but the team below is only built when a model id
    # is set too. Without this check that pair blanks the registry: no deterministic team because a
    # key is attached, no Nemotron team because it has no model, and a backend that cannot start.
    # `build_services` has already checked this; repeated here so a direct caller is covered too.
    check_keyed_deployment(settings)

    if not nvidia_key:
        deterministic = MockAnalyst(scenarios, implementor, settings.agent_may_implement)
        deterministic_reviewer = MockReviewer()
        teams["mock"] = AgentTeam(analyst=deterministic, fallback=None, reviewer=deterministic_reviewer)
        if settings.episode_fallback_to_mock:
            fallback_analyst = deterministic
            fallback_reviewer = deterministic_reviewer

    if claude_key and settings.claude_model:
        claude = ClaudeClient(
            settings.claude_base_url,
            settings.claude_model,
            claude_key,
            settings.anthropic_workspace_id,
        )
        teams["claude"] = AgentTeam(
            analyst=ModelAnalyst(
                "claude",
                claude,
                settings.mcp_url or mcp_server,
                settings.episode_agent_timeout_s,
                settings.agent_may_implement,
            ),
            fallback=fallback_analyst,
            reviewer=ModelReviewer("claude", claude, fallback_reviewer),
            analyst_model=settings.claude_model,
            reviewer_model=settings.claude_model,
        )

    if nvidia_key and settings.nemotron_model and settings.nemotron_reviewer_model:
        reviewer = NimClient(
            settings.nemotron_base_url,
            settings.nemotron_reviewer_model,
            nvidia_key,
            settings.nemotron_timeout_s,
            json_mode=True,
        )
        provider = NemotronAgentProvider(
            settings.nemotron_base_url,
            settings.nemotron_model,
            nvidia_key,
            settings.scenario_max_candidates,
            settings.nemotron_timeout_s,
        )
        teams["nemotron"] = AgentTeam(
            analyst=PipelineAnalyst(
                "nemotron",
                provider,
                scenarios,
                implementor,
                settings.episode_agent_timeout_s,
                settings.agent_may_implement,
            ),
            fallback=fallback_analyst,
            reviewer=ModelReviewer("nemotron", reviewer, fallback_reviewer),
            analyst_model=settings.nemotron_model,
            reviewer_model=settings.nemotron_reviewer_model,
        )

    wanted = settings.episode_analyst
    if wanted == "auto":
        selected = "nemotron" if "nemotron" in teams else "claude" if "claude" in teams else "mock"
    else:
        selected = wanted
    if selected not in teams:
        if selected == "mock":
            # Not a missing credential: the local team is withheld on purpose while a key is attached.
            raise RuntimeError(
                "the deterministic analyst is not offered while NVIDIA_API_KEY is attached; "
                "select nemotron or claude, or remove the key to run the local team"
            )
        needed = "an Anthropic API key" if selected == "claude" else "an NVIDIA API key"
        raise RuntimeError(f"analyst {selected} requires {needed}")
    return teams, selected


# traci registers labelled connections in process-global state, so these counters outlive any one service
# graph. A map switch overlaps the old and new live simulations, and a reused label is rejected outright
# ("Connection 'live-1' already active"), which would make every runtime switch fail.
_live_labels = count(1)
_branch_labels = count(1)


def build_services(settings: Settings, hub: ConnectionHub, mcp_server: MCPServer) -> Services:
    check_keyed_deployment(settings)
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
