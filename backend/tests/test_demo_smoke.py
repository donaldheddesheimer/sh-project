"""Demo smoke tests: the real app on real SUMO, driven over REST the way the console drives it.

Each test boots its own city and its own memory. They are slow (about a minute each, an estimate) and every wait is
generous, so a failure is a stall or an error in the app and never the clock: the timeout message shows the last
run or episode state seen.
"""

import time
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

_RUN_FINAL = ("completed", "failed")
_EPISODE_FINAL = ("completed", "superseded", "aborted", "failed")
_VERDICTS = ("effective", "ineffective", "inconclusive")


def _poll(fetch, timeout, what, done=bool, describe=str, interval=1.0):
    """Call ``fetch`` until ``done(result)``; on timeout fail with the last result seen."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fetch()
        if done(last):
            return last
        time.sleep(interval)
    raise AssertionError(f"gave up after {timeout:.0f}s waiting for {what}; last seen: {describe(last)}")


def _describe_run(run) -> str:
    candidates = ", ".join(f"{c['id']}={c['status']}" for c in run.get("candidates", []))
    return f"status={run.get('status')} error={run.get('error')} candidates=[{candidates}]"


def _describe_episode(episode) -> str:
    steps = " | ".join(f"{s['status']}: {s['message']}" for s in episode.get("steps", []))
    return f"status={episode.get('status')} error={episode.get('error')} steps=[{steps}]"


@contextmanager
def _running_app(scenario, tmp_path: Path, **overrides):
    """A fresh app on the grid, its first frame served. Every setting a developer's .env or shell could change
    is pinned, so the test cannot depend on them; init arguments outrank the environment."""
    pinned = dict(
        scenario_dir=scenario.directory,
        smart_city_provider="mock",
        agent_provider="mock",
        episode_analyst="mock",  # a real NVIDIA_API_KEY must not select Nemotron
        demo_script=None,
        memory_enabled=True,
        memory_dir=tmp_path / "memory",
        snapshot_dir=tmp_path / "snapshots",
        incident_detection_delay_s=2.0,
        sim_autostart=True,
        sumo_gui=False,
        scenario_workers=4,
        scenario_max_candidates=9,
        analysis_live_speed=None,
        agent_may_implement=True,
    )
    pinned.update(overrides)
    with TestClient(create_app(Settings(_env_file=None, **pinned))) as client:
        _poll(lambda: client.get("/api/state").status_code == 200, 180, "the first simulation frame")
        yield client


def test_analyze_response_recommends_and_rejects_the_unsafe_plan(scenario, tmp_path):
    with _running_app(scenario, tmp_path, sim_warmup_s=120, sim_speed=16) as client:
        assert client.post("/api/incidents/inject", json={"type": "collision"}).status_code == 202
        _poll(lambda: client.get("/api/incidents").json(), 120, "the crash to be detected")

        started = client.post("/api/scenarios/run", json={"horizon_s": 300})
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        run = _poll(
            lambda: client.get(f"/api/scenarios/{run_id}").json(),
            300,
            f"{run_id} to finish",
            done=lambda r: r.get("status") in _RUN_FINAL,
            describe=_describe_run,
        )

        assert run["status"] == "completed", _describe_run(run)
        candidates = {c["id"]: c for c in run["candidates"]}
        assert run["candidates"][0]["id"] == "baseline" and candidates["baseline"]["status"] == "completed"
        # the deliberately unsafe plan is always proposed, and the validator always turns it away
        assert candidates["aggressive-flush"]["status"] == "rejected"
        assert candidates["aggressive-flush"]["violations"]
        assert candidates[run["recommendation"]["candidate_id"]]["status"] == "completed"


def test_autonomous_episode_runs_end_to_end_with_the_mock(scenario, tmp_path):
    # crash-ahead fires at simulation second 420, after the 300 s warm-up; the setting shortens its 600 s monitor window
    with _running_app(
        scenario, tmp_path, sim_warmup_s=300, sim_speed=8, scenario_horizon_s=300, episode_monitor_s=120
    ) as client:
        started = client.post("/api/demo/start", json={"script": "crash-ahead"})
        assert started.status_code == 202, started.text
        episode_id = started.json()["id"]
        episode = _poll(
            lambda: client.get(f"/api/episodes/{episode_id}").json(),
            420,
            f"{episode_id} to finish",
            done=lambda e: e.get("status") in _EPISODE_FINAL,
            describe=_describe_episode,
        )

        assert episode["status"] == "completed", _describe_episode(episode)
        implementation = episode["implementation"]
        assert implementation["run_id"] == episode["run_id"] and implementation["implemented_by"] == "agent"
        assert episode["lesson"]["verdict"] in _VERDICTS
        assert Path(episode["memory_path"]).is_file()

        memory = client.get("/api/memory").json()
        assert memory["episodes"] == 1 and memory["latest"] == [episode_id]
        run = client.get(f"/api/scenarios/{episode['run_id']}").json()
        assert run["status"] == "completed" and run["implementation"] is not None
