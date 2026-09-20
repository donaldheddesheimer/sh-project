"""Bundle real Oakland building and landscape footprints for the offline demo map.

Run from the repository root with `python3 frontend/scripts/build_oakland_context.py`.
This fetches only static OpenStreetMap geometry; live traffic still comes from SUMO.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


BOUNDARY = "40.437,-79.962,40.449,-79.944"  # south,west,north,east; Oakland scenario box
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT = Path(__file__).resolve().parents[1] / "public" / "oakland-context.geojson"
QUERY = f"""[out:xml][timeout:90];
(
  way["building"]({BOUNDARY});
  way["leisure"="park"]({BOUNDARY});
  way["landuse"~"^(grass|recreation_ground)$"]({BOUNDARY});
  way["natural"="water"]({BOUNDARY});
);
(._;>;);
out body;
"""


def main() -> None:
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": QUERY}).encode(),
        headers={"User-Agent": "traffic-ops-demo/1.0"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        root = ET.fromstring(response.read())

    nodes = {
        node.attrib["id"]: [float(node.attrib["lon"]), float(node.attrib["lat"])]
        for node in root.findall("node")
    }
    features = []
    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if "building" in tags:
            kind = "building"
        elif tags.get("natural") == "water":
            kind = "water"
        elif tags.get("leisure") == "park" or tags.get("landuse") in {"grass", "recreation_ground"}:
            kind = "park"
        else:
            continue
        refs = [node.attrib["ref"] for node in way.findall("nd")]
        if len(refs) < 4 or refs[0] != refs[-1] or any(ref not in nodes for ref in refs):
            continue  # open ways and multipolygon relations are not valid standalone footprints
        features.append({
            "type": "Feature",
            "id": way.attrib["id"],
            "properties": {"kind": kind},
            "geometry": {"type": "Polygon", "coordinates": [[nodes[ref] for ref in refs]]},
        })

    if not any(feature["properties"]["kind"] == "building" for feature in features):
        raise RuntimeError("Overpass returned no building footprints; refusing to replace the bundled map")
    OUTPUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")) + "\n")
    print(f"Wrote {OUTPUT} with {len(features)} OSM footprints")


if __name__ == "__main__":
    main()
