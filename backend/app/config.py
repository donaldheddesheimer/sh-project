"""Application defaults plus the model credentials allowed from the environment."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# The only names read from the environment or a .env file. The workspace id is part of an
# Anthropic credential: an organization-level key cannot authenticate without it.
_CREDENTIAL_KEYS = {"anthropic_api_key", "anthropic_workspace_id", "nvidia_api_key"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Keep deployment behavior in code/UI; environment files are credentials only."""

        def api_keys_only() -> dict[str, object]:
            values = {**dotenv_settings(), **env_settings()}
            return {key: value for key, value in values.items() if key in _CREDENTIAL_KEYS}

        return init_settings, api_keys_only, file_secret_settings

    # --- provider selection -------------------------------------------------
    smart_city_provider: Literal["mock", "nvidia"] = "mock"
    agent_provider: Literal["mock", "nemotron"] = "nemotron"

    # --- traffic simulation -------------------------------------------------
    # downtown_grid (the tests and startup use it) or pittsburgh_oakland; runtime UI/API switching is primary
    scenario_dir: Path = REPO_ROOT / "simulation" / "scenarios" / "downtown_grid"
    sumo_binary: str | None = None  # default: bundled eclipse-sumo, then PATH
    sumo_gui: bool = False  # open sumo-gui for the live simulation (debugging)
    sim_speed: float = 4.0  # live simulation speed as a multiple of wall-clock time
    sim_warmup_s: float = 300.0  # simulated seconds run at startup/reset so roads are populated
    sim_autostart: bool = True
    broadcast_hz: float = 8.0  # max WebSocket state frames per second
    snapshot_dir: Path = Path(tempfile.gettempdir()) / "traffic-ops-snapshots"

    # --- scenario analysis ("Analyze Response") -----------------------------
    scenario_horizon_s: float = 600.0  # simulated seconds per branch when the request omits it
    scenario_workers: int = 4  # SUMO branches simulated in parallel
    scenario_sample_s: float = 30.0  # simulated seconds between a candidate's timeline samples
    scenario_max_candidates: int = 9  # including the baseline; the mock proposes exactly this many
    scenario_history: int = 10  # runs kept in memory for GET /api/scenarios
    scenario_idle_timeout_s: float = 300.0  # an MCP agent's open run fails after this long without a tool call

    # --- autonomous demo episode (app/learning/) ----------------------------
    demo_script: str | None = None  # arm this demos/*.json script at startup
    # Prefer a configured model, then keep local development and credential-free deployments runnable with Mock.
    episode_analyst: Literal["auto", "mock", "claude", "nemotron"] = "auto"
    episode_monitor_s: float | None = None  # sim seconds a plan is watched (default: script monitor_s, else horizon)
    episode_agent_timeout_s: float = 420.0  # wall-clock limit for one model-backed analyst run
    episode_fallback_to_mock: bool = False  # selected model failures stay visible; choose Mock explicitly if desired
    episode_pause_on_finish: bool = True  # pause the live simulation when an episode completes
    agent_may_implement: bool = True  # allow the MCP implement_recommendation tool (the operator path is separate)
    memory_enabled: bool = True  # store lessons and recall them for the next incident
    memory_dir: Path = REPO_ROOT / "memory"
    mcp_url: str | None = None  # where a model loop reaches /mcp; unset = this app's MCP server, in-process

    # --- memory and agents --------------------------------------------------
    embedding_model: str | None = None  # unset keeps recall structured-only
    embedding_base_url: str | None = None  # defaults to NEMOTRON_BASE_URL after settings load
    agent_fallback_to_mock: bool = False  # model failures stay visible instead of changing providers silently

    # --- mock Smart City provider ------------------------------------------
    incident_detection_delay_s: float = 4.0  # simulated seconds between a crash and its detection

    # --- NVIDIA integration (unused by the mock providers) ------------------
    nvidia_api_key: SecretStr | None = None
    nvidia_va_mcp_url: str | None = None  # VSS Video Analytics MCP server
    vss_replay_file: Path | None = None  # development-only VSS-shaped incident timeline
    vss_poll_s: float = 5.0
    vss_match_max_dist_m: float = 40.0
    vss_require_vlm_confirmation: bool = True
    vss_default_severity: Literal["minor", "major", "critical"] = "major"
    nemotron_base_url: str = "https://integrate.api.nvidia.com/v1"  # NIM, OpenAI-compatible
    nemotron_model: str | None = "nvidia/nemotron-3-super-120b-a12b"

    # --- Anthropic integration (unused unless Claude is configured) --------
    anthropic_api_key: SecretStr | None = None
    anthropic_workspace_id: str | None = None  # required by organization-level keys; scoped keys omit it
    claude_base_url: str = "https://api.anthropic.com"
    claude_model: str | None = "claude-haiku-4-5-20251001"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("scenario_dir")
    @classmethod
    def _from_repo_root(cls, value: Path) -> Path:
        # Programmatic overrides may be relative; make them independent of the caller's cwd.
        return value if value.is_absolute() else REPO_ROOT / value

    @field_validator("vss_replay_file")
    @classmethod
    def _replay_from_repo_root(cls, value: Path | None) -> Path | None:
        return value if value is None or value.is_absolute() else REPO_ROOT / value

    @model_validator(mode="after")
    def _default_embedding_base_url(self) -> Settings:
        if self.embedding_base_url is None:
            self.embedding_base_url = self.nemotron_base_url
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
