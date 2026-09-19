from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from app.config import Settings
from app.simulation.network import RoadNetwork
from app.simulation.scenario import Scenario, load_scenario
from app.simulation.sumo import SumoSimulation

_labels = itertools.count()


@pytest.fixture(scope="session")
def scenario() -> Scenario:
    return load_scenario(Settings().scenario_dir)


@pytest.fixture(scope="session")
def network(scenario: Scenario) -> RoadNetwork:
    return RoadNetwork(scenario)


@pytest.fixture
def make_sim(scenario: Scenario, network: RoadNetwork, tmp_path: Path):
    """Factory for started simulations; all are closed at teardown."""
    started: list[SumoSimulation] = []

    def make(warmup_s: float = 0.0) -> SumoSimulation:
        sim = SumoSimulation(scenario, network, label=f"test-{next(_labels)}", snapshot_dir=tmp_path)
        sim.start()
        started.append(sim)
        if warmup_s:
            sim.run_for(warmup_s)
        return sim

    yield make
    for sim in started:
        sim.close()
