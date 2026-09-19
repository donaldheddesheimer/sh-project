# Oakland, Pittsburgh network

The street network of central Oakland (Fifth and Forbes Avenues, the Pitt/CMU/UPMC area),
built from OpenStreetMap for the `pittsburgh_oakland` scenario.

**Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available
under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).**
`oakland.osm` is an unmodified Overpass extract (base `2026-09-19T20:11:25Z`, query in
`oakland.overpass`). `oakland.net.xml` is derived from it, so the same credit applies. The
console shows it on the map (the `attribution` field of the scenario's `scenario.json`).

Only the street layout, lane counts, speed limits and signal locations come from OSM. Traffic
demand and signal timing are **synthetic**: no counts or city timing sheets were used.

| File | |
|---|---|
| `oakland.overpass` | Overpass query: drivable roads, signal nodes, fire stations, hospitals |
| `oakland.osm` | the extract (≈300 kB) |
| `build_network.py` | OSM → `oakland.net.xml`; also regenerates the scenario's demand, which the timing is sized to |
| `oakland.net.xml` | 332 edges, 33 signals, 54 s coordinated cycle (one signal runs 52 s) |

Rebuild with `make network-oakland`, or add `--download` to refresh the OSM extract first. The
build is deterministic: the same extract gives the same network and demand. See the docstring
of `build_network.py` for each step and why it is there.
