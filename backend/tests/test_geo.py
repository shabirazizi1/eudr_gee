import math

from app import geo


def test_area_of_known_square_within_one_percent():
    # A 0.01 deg square at the equator, where meridian and parallel distances per degree are
    # equal (cos(lat)=1) -- an independent flat-earth estimate to check the geodesic formula against.
    side_deg = 0.01
    ring = [[0, 0], [side_deg, 0], [side_deg, side_deg], [0, side_deg], [0, 0]]
    square = {"type": "Polygon", "coordinates": [ring]}

    side_m = math.radians(side_deg) * geo.R
    expected_ha = (side_m ** 2) / 10_000

    actual_ha = geo.area_ha(square)
    assert abs(actual_ha - expected_ha) / expected_ha < 0.01


def test_rejects_self_intersecting_polygon():
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    errors = geo.validate(bowtie)
    assert errors
    assert any("crosses itself" in e for e in errors)


def test_rejects_fewer_than_three_points():
    too_few = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}
    errors = geo.validate(too_few)
    assert errors
    assert any("at least 3 points" in e for e in errors)


def test_accepts_a_valid_square():
    square = {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]]}
    assert geo.validate(square) == []
