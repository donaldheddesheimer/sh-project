"""Scenario definition: which SUMO config to run plus demo metadata."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

from app.models.domain import GeoPoint, Severity


class EmsStation(BaseModel):
    id: str
    name: str
    edge: str


class CollisionDefaults(BaseModel):
    edge: str
    lanes: list[int] = [0]
    position_fraction: float = 0.55
    severity: Severity = Severity.MAJOR


class DemoCrash(BaseModel):
    """One scripted collision. Unset fields fall back to the scenario's ``default_collision``."""

    at_sim_s: float = Field(ge=0, description="Absolute simulation time the crash happens")
    segment_id: str | None = None
    lanes: list[int] | None = None
    position_fraction: float | None = Field(None, ge=0.1, le=0.9)
    severity: Severity | None = None


class DemoScript(BaseModel):
    """A demo episode: scheduled crashes, or an armed operator-controlled collision.

    A crash before the end of warm-up has already happened when the console opens (it is injected while the
    simulation warms up, so a queue is already forming); a later one happens while the demo runs. No crashes
    means wait for the operator to inject one. Two crashes close together show the agent being superseded by one
    that sees both.
    """

    id: str
    name: str
    description: str = ""
    # An empty list is an operator-controlled demo: it arms autonomous response without scheduling a crash,
    # so the presenter can start the episode with the console's Inject collision button.
    crashes: list[DemoCrash] = Field(default_factory=list)
    monitor_s: float | None = Field(None, ge=60, description="Sim seconds the applied plan is watched (default: horizon)")
    speed: float | None = Field(None, gt=0, le=64, description="Live speed multiplier to run the demo at")


class Scenario(BaseModel):
    id: str
    name: str
    description: str = ""
    attribution: str | None = None  # data credit the map must show (OSM data is ODbL)
    directory: Path
    sumocfg: Path
    net_file: Path
    geo_origin: GeoPoint
    ems_stations: list[EmsStation]
    default_collision: CollisionDefaults
    # Clockwise rotation (degrees) applied to travel bearings before they are bucketed into
    # NB/EB/SB/WB, for street grids that run off true north (Oakland, Pittsburgh: 45).
    heading_offset_deg: float = 0.0
    demos: dict[str, DemoScript] = Field(default_factory=dict)


def load_demo_scripts(directory: Path) -> dict[str, DemoScript]:
    """Every ``demos/*.json`` next to the scenario, keyed by script id (file name order)."""
    scripts: dict[str, DemoScript] = {}
    for path in sorted((directory / "demos").glob("*.json")):
        script = DemoScript(**json.loads(path.read_text()))
        scripts[script.id] = script
    return scripts


def load_scenario(directory: Path) -> Scenario:
    directory = directory.resolve()
    meta = json.loads((directory / "scenario.json").read_text())
    sumocfg = (directory / meta["sumocfg"]).resolve()
    net_value = ET.parse(sumocfg).getroot().find("./input/net-file").get("value")
    return Scenario(
        id=meta["id"],
        name=meta["name"],
        description=meta.get("description", ""),
        attribution=meta.get("attribution"),
        directory=directory,
        sumocfg=sumocfg,
        net_file=(sumocfg.parent / net_value).resolve(),
        geo_origin=GeoPoint(**meta["geo_origin"]),
        ems_stations=[EmsStation(**s) for s in meta.get("ems_stations", [])],
        default_collision=CollisionDefaults(**meta["default_collision"]),
        heading_offset_deg=meta.get("heading_offset_deg", 0.0),
        demos=load_demo_scripts(directory),
    )
