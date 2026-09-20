"""Runtime configuration, read from environment variables (or a .env file)."""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- provider selection -------------------------------------------------
    smart_city_provider: Literal["mock", "nvidia"] = "mock"
    agent_provider: Literal["mock", "nemotron"] = "mock"

    # --- traffic simulation -------------------------------------------------
    # downtown_grid (the tests use it) or pittsburgh_oakland; a relative path is taken from the repo root
    scenario_dir: Path = REPO_ROOT / "simulation" / "scenarios" / "downtown_grid"
    sumo_binary: str | None = None  # default: bundled eclipse-sumo, then $SUMO_HOME, then PATH
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

    # --- twin engine --------------------------------------------------------
    # Branches are CPU-hungry (scenario_workers SUMO processes at once) and the live simulation shares the
    # machine with them. Slowing the live city while a run is open trades wall-clock realism for branches
    # that finish sooner. None leaves the speed alone; the operator's own speed change always wins. Bounded like
    # POST /api/simulation/speed (SpeedRequest): 0 would divide by zero in the runner and put the live city in error.
    analysis_live_speed: float | None = Field(None, gt=0, le=64)

    # --- autonomous demo episode (app/learning/) ----------------------------
    demo_script: str | None = None  # arm this demos/*.json script at startup
    episode_analyst: Literal["auto", "mock", "nemotron"] = "auto"  # auto: nemotron if NVIDIA_API_KEY + NEMOTRON_MODEL
    episode_monitor_s: float | None = None  # sim seconds a plan is watched (default: script monitor_s, else horizon)
    episode_agent_timeout_s: float = 300.0  # wall-clock limit for one Nemotron analyst run
    episode_fallback_to_mock: bool = True  # run the mock analyst when the Nemotron loop fails
    episode_pause_on_finish: bool = True  # pause the live simulation when an episode completes
    agent_may_implement: bool = True  # allow the MCP implement_recommendation tool (the operator path is separate)
    memory_enabled: bool = True  # store lessons and recall them for the next incident
    memory_dir: Path = REPO_ROOT / "memory"
    mcp_url: str | None = None  # where the Nemotron loop reaches /mcp; unset = this app's MCP server, in-process

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
    nemotron_model: str | None = None

    # NoDecode: pydantic-settings json.loads() a list-typed env var inside the settings source,
    # before any validator runs, so a comma-separated CORS_ORIGINS would raise there rather than
    # reach _split_cors_origins. NoDecode hands the raw string to the validator instead.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("scenario_dir")
    @classmethod
    def _from_repo_root(cls, value: Path) -> Path:
        # `make backend` runs from backend/, so a cwd-relative SCENARIO_DIR would depend on how it was started
        return value if value.is_absolute() else REPO_ROOT / value

    @field_validator("vss_replay_file")
    @classmethod
    def _replay_from_repo_root(cls, value: Path | None) -> Path | None:
        return value if value is None or value.is_absolute() else REPO_ROOT / value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        # Because of NoDecode this sees the raw env string, so it accepts both forms: the JSON
        # list pydantic-settings would otherwise have parsed, and a plain comma-separated list,
        # which is what a hosting panel or `gcloud run deploy --set-env-vars` can actually carry.
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
