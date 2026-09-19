"""Build the Oakland (Pittsburgh) SUMO network from the committed OpenStreetMap extract.

Only the street network is real: `oakland.osm` is an OpenStreetMap extract (© OpenStreetMap
contributors, ODbL; see README.md). Traffic demand is synthetic (see the scenario's
build_demand.py) and signal timing is generated, not taken from city timing sheets:

1. netconvert: OSM -> SUMO net, passenger roads only, clipped to the Oakland box. Signals come
   from OSM `traffic_signals` nodes, joined per intersection. Protected-left phases are disabled
   (`--tls.left-green.time 0`) so every green is followed by yellow then all-red, the transition
   the safety validator's `no_clearance` rule requires.
2. The scenario's synthetic demand is regenerated on that net (signal timing is sized to it).
3. Webster timing from one routed hour of that demand (SUMO tools/tlsCycleAdaptation.py): greens
   of at least 12 s (the validator's pedestrian floor), cycles of 50-120 s, one unified cycle.
4. Coordinated offsets along the main flows (SUMO tools/tlsCoordinator.py).
5. Signal ids: netconvert names guessed signals `GS_<node>`; the backend assumes a signal's id
   is its junction id, so the prefix is dropped.

Usage:
    python simulation/networks/pittsburgh_oakland/build_network.py              # rebuild from oakland.osm
    python simulation/networks/pittsburgh_oakland/build_network.py --download   # refresh oakland.osm first
Requires SUMO (`pip install eclipse-sumo`).
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OSM = HERE / "oakland.osm"
QUERY = HERE / "oakland.overpass"
NET = HERE / "oakland.net.xml"
SCENARIO = REPO / "simulation" / "scenarios" / "pittsburgh_oakland"
DEMAND = SCENARIO / "demand.rou.xml"
VTYPES = SCENARIO / "vtypes.add.xml"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# west,south,east,north (lon/lat): central Oakland around Fifth and Forbes Avenues; the same box as oakland.overpass
BOUNDARY = "-79.962,40.437,-79.944,40.449"

YELLOW_S = 3
ALL_RED_S = 2
MIN_GREEN_S = 12  # the validator's vehicle/pedestrian green floor
MIN_CYCLE_S = 50
MAX_CYCLE_S = 120

NETCONVERT_OPTIONS = [
    "--proj.utm",
    "--geometry.remove",
    "--roundabouts.guess",
    "--ramps.guess",
    "--junctions.join",
    "--tls.guess-signals",
    "--tls.discard-simple",
    "--tls.default-type", "static",
    "--tls.yellow.time", str(YELLOW_S),
    "--tls.allred.time", str(ALL_RED_S),
    "--tls.cycle.time", "90",  # replaced by Webster timing below
    "--tls.left-green.time", "0",  # no mixed green+yellow protected-left phases (see step 1)
    "--keep-edges.by-vclass", "passenger",
    "--remove-edges.isolated",
    "--keep-edges.components", "1",
    "--keep-edges.in-geo-boundary", BOUNDARY,
    "--output.street-names",
    "--output.original-names",
]


def sumo_home() -> Path:
    if os.environ.get("SUMO_HOME"):
        return Path(os.environ["SUMO_HOME"])
    try:
        import sumo  # type: ignore  # provided by `pip install eclipse-sumo`

        return Path(sumo.SUMO_HOME)
    except ImportError:
        sys.exit("SUMO not found: `pip install eclipse-sumo` or set SUMO_HOME")


def find_tool(name: str) -> str:
    # `which` with an explicit path also resolves name.exe on Windows
    exe = shutil.which(name) or shutil.which(name, path=str(sumo_home() / "bin"))
    if not exe:
        sys.exit(f"{name} not found: `pip install eclipse-sumo` or install SUMO and add it to PATH")
    return exe


def run(cmd: list[str]) -> None:
    env = {**os.environ, "SUMO_HOME": str(sumo_home())}
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")


def download() -> None:
    """Refresh oakland.osm from the Overpass API (the query is oakland.overpass)."""
    data = urllib.parse.urlencode({"data": QUERY.read_text()}).encode()
    # Overpass answers 406 to clients without a User-Agent
    request = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": "traffic-ops-demo/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        OSM.write_bytes(response.read())
    print(f"downloaded {OSM.relative_to(REPO)} ({OSM.stat().st_size // 1024} kB)")


def drop_guessed_signal_prefix(net_file: Path) -> int:
    """Rename `GS_<node>` signals to `<node>` (in tlLogic ids and every connection's `tl`)."""
    text = net_file.read_text()
    tls_ids = set(re.findall(r'<tlLogic id="([^"]+)"', text))
    junctions = set(re.findall(r'<junction id="([^"]+)"', text))
    renames = [t for t in tls_ids if t.startswith("GS_")]
    for old in renames:
        new = old[3:]
        if new not in junctions or new in tls_ids:
            sys.exit(f"cannot rename signal {old}: {new} is not a free junction id")
    net_file.write_text(re.sub(r'((?:<tlLogic id|\btl)=")GS_([^"]+)"', r'\1\2"', text))
    return len(renames)


def build(scale: float | None) -> None:
    netconvert, duarouter = find_tool("netconvert"), find_tool("duarouter")
    tools = sumo_home() / "tools"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw, webster = tmp / "raw.net.xml", tmp / "webster.net.xml"
        routes, cycle_plan, offsets = tmp / "routes_1h.rou.xml", tmp / "webster.add.xml", tmp / "offsets.add.xml"

        run([netconvert, "--osm-files", str(OSM), "-o", str(raw), "--no-warnings", *NETCONVERT_OPTIONS])

        demand_cmd = [sys.executable, str(SCENARIO / "build_demand.py"), "--net", str(raw)]
        run(demand_cmd + (["--scale", str(scale)] if scale is not None else []))

        run([duarouter, "-n", str(raw), "--route-files", str(DEMAND), "--additional-files", str(VTYPES),
             "-o", str(routes), "--begin", "0", "--end", "3600", "--seed", "42", "--no-warnings", "--no-step-log"])
        run([sys.executable, str(tools / "tlsCycleAdaptation.py"), "-n", str(raw), "-r", str(routes), "-b", "0",
             "-o", str(cycle_plan), "-y", str(YELLOW_S), "-g", str(MIN_GREEN_S),
             "--min-cycle", str(MIN_CYCLE_S), "--max-cycle", str(MAX_CYCLE_S), "-u", "-p", "0"])
        run([netconvert, "-s", str(raw), "--tllogic-files", str(cycle_plan), "-o", str(webster), "--no-warnings"])
        run([sys.executable, str(tools / "tlsCoordinator.py"), "-n", str(webster), "-r", str(routes),
             "-o", str(offsets)])
        run([netconvert, "-s", str(webster), "--tllogic-files", str(offsets), "-o", str(NET), "--no-warnings"])

    renamed = drop_guessed_signal_prefix(NET)
    summarize(renamed)


def summarize(renamed: int) -> None:
    import sumolib  # bundled with eclipse-sumo

    net = sumolib.net.readNet(str(NET), withPrograms=True)
    cycles = collections.Counter(
        round(sum(phase.duration for phase in program.getPhases()))
        for tls in net.getTrafficLights()
        for program in tls.getPrograms().values()
    )
    edges = net.getEdges(withInternal=False)
    print(
        f"wrote {NET.relative_to(REPO)}: {len(edges)} edges, {len(net.getTrafficLights())} signals "
        f"({renamed} renamed from GS_*), cycles {dict(cycles)}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--download", action="store_true", help="refresh oakland.osm from Overpass first")
    parser.add_argument("--scale", type=float, default=None, help="demand scale (default: build_demand.py's)")
    args = parser.parse_args()
    if args.download:
        download()
    build(args.scale)
