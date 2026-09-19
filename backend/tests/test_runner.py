import asyncio
import time

from app.models.api import RunStatus
from app.models.domain import NetworkState, TrafficMetrics
from app.simulation.runner import LiveFrame, LiveSimulationRunner


class FlakySimulation:
    """Minimal stand-in for TrafficSimulation that fails once after a few steps."""

    step_length = 0.5
    failures_left = 1

    def __init__(self) -> None:
        self.time = 0.0
        self.closed = False

    def start(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def run_for(self, seconds: float) -> None:
        self.time += seconds

    def step(self) -> None:
        self.time += self.step_length
        if self.time > 3 and FlakySimulation.failures_left:
            FlakySimulation.failures_left -= 1
            raise RuntimeError("TraCI connection lost")

    def get_network_state(self) -> NetworkState:
        metrics = TrafficMetrics(sim_time=self.time, mean_vehicle_delay=0, max_queue_length=0, throughput=0, mean_speed=0)
        return NetworkState(
            sim_time=self.time, intersections=[], segments=[], vehicles=[], emergency_vehicles=[], metrics=metrics
        )


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_runner_reports_failure_and_recovers_on_reset():
    frames: list[LiveFrame] = []
    runner = LiveSimulationRunner(FlakySimulation, on_frame=frames.append, speed=64, warmup_s=0, broadcast_hz=50)
    runner.start()
    try:
        _wait(lambda: runner.status is RunStatus.ERROR)
        assert frames[-1].error == "TraCI connection lost"
        asyncio.run(runner.reset())
        assert runner.status is RunStatus.RUNNING
        _wait(lambda: frames[-1].state is not None and frames[-1].status is RunStatus.RUNNING)
        assert frames[-1].error is None
    finally:
        runner.shutdown()
