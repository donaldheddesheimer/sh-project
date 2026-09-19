"""Scenario definition: which SUMO config to run plus demo metadata."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel

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


class Scenario(BaseModel):
    id: str
    name: str
    description: str = ""
    directory: Path
    sumocfg: Path
    net_file: Path
    geo_origin: GeoPoint
    ems_stations: list[EmsStation]
    default_collision: CollisionDefaults


def load_scenario(directory: Path) -> Scenario:
    directory = directory.resolve()
    meta = json.loads((directory / "scenario.json").read_text())
    sumocfg = (directory / meta["sumocfg"]).resolve()
    net_value = ET.parse(sumocfg).getroot().find("./input/net-file").get("value")
    return Scenario(
        id=meta["id"],
        name=meta["name"],
        description=meta.get("description", ""),
        directory=directory,
        sumocfg=sumocfg,
        net_file=(sumocfg.parent / net_value).resolve(),
        geo_origin=GeoPoint(**meta["geo_origin"]),
        ems_stations=[EmsStation(**s) for s in meta.get("ems_stations", [])],
        default_collision=CollisionDefaults(**meta["default_collision"]),
    )
