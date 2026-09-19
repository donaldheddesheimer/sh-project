"""Runtime configuration, read from environment variables (or a .env file)."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- provider selection -------------------------------------------------
    smart_city_provider: Literal["mock", "nvidia"] = "mock"
    agent_provider: Literal["mock", "nemotron"] = "mock"

    # --- traffic simulation -------------------------------------------------
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
    scenario_max_candidates: int = 8  # including the baseline
    scenario_history: int = 10  # runs kept in memory for GET /api/scenarios

    # --- mock Smart City provider ------------------------------------------
    incident_detection_delay_s: float = 4.0  # simulated seconds between a crash and its detection

    # --- NVIDIA integration (unused by the mock providers) ------------------
    nvidia_api_key: SecretStr | None = None
    nvidia_va_mcp_url: str | None = None  # VSS Video Analytics MCP server
    nemotron_base_url: str = "https://integrate.api.nvidia.com/v1"  # NIM, OpenAI-compatible
    nemotron_model: str | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
