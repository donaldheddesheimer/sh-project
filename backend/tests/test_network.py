from app.models.domain import PhaseKind
from app.simulation.network import RoadNetwork, heading_direction


def test_grid_topology(network: RoadNetwork):
    assert len(network.intersections) == 9
    assert len(network.segments) == 48
    assert all(i.tls_id for i in network.intersections.values())
    assert network.intersections["B2"].name == "Central Ave & Main St"
    b2_c2 = network.segments["B2_C2"]
    assert (b2_c2.name, b2_c2.direction, b2_c2.lanes) == ("Main St", "EB", 2)


def test_every_approach_has_signalled_through_movement(network: RoadNetwork):
    for info in network.intersections.values():
        assert set(info.approaches) == {"NB", "SB", "EB", "WB"}
        for approach in info.approaches.values():
            assert approach.through_link_indices, (info.id, approach.direction)


def test_base_program_phases_are_labelled(network: RoadNetwork):
    program = network.base_program("B2")
    kinds = [p.kind for p in program.phases]
    assert kinds.count(PhaseKind.GREEN) == 2
    assert program.cycle_length == 90
    greens = [p for p in program.phases if p.kind is PhaseKind.GREEN]
    assert {tuple(p.served_approaches) for p in greens} == {("NB", "SB"), ("EB", "WB")}


def test_geometry_is_georeferenced_near_origin(network: RoadNetwork):
    geo = network.geometry()
    lon, lat = geo.center
    origin = network.scenario.geo_origin
    assert abs(lat - origin.lat) < 0.01 and abs(lon - origin.lon) < 0.01
    assert len(geo.stations) == 1 and geo.stations[0].segment_id == "W2_A2"
    assert all(len(i.approaches) == 4 for i in geo.intersections)


def test_heading_direction():
    assert heading_direction(10, 1) == "EB"
    assert heading_direction(-10, 1) == "WB"
    assert heading_direction(1, 10) == "NB"
    assert heading_direction(1, -10) == "SB"
