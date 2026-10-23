import math

import pytest

from geotrack.geo import MAX_VIEWPORT_CELLS, Viewport, cell_of


@pytest.mark.parametrize(
    "lat, lon, size, expected",
    [
        (50.45, 30.52, 0.25, (201, 122)),
        (-0.1, -0.1, 0.25, (-1, -1)),
        (0.0, 0.0, 0.25, (0, 0)),
        (90.0, 180.0, 1.0, (90, 180)),
    ],
)
def test_cell_of(
    lat: float, lon: float, size: float, expected: tuple[int, int]
) -> None:
    assert cell_of(lat, lon, size) == expected


def test_parse_accepts_numbers_and_ints() -> None:
    assert Viewport.parse([30, 50.4, 30.6, 50.5]) == Viewport(30.0, 50.4, 30.6, 50.5)


@pytest.mark.parametrize(
    "raw, message",
    [
        ([1, 2, 3], r"\[west, south, east, north\]"),
        ("1,2,3,4", r"\[west, south, east, north\]"),
        ([1, "2", 3, 4], "numbers"),
        ([1, True, 3, 4], "numbers"),
        ([0, math.nan, 1, 1], "finite"),
        ([0, 10, 1, 5], "south <= north"),
        ([0, -91, 1, 5], "south <= north"),
        ([-181, 0, 1, 5], "longitudes"),
    ],
)
def test_parse_rejects(raw: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Viewport.parse(raw)


def test_covers_plain_box() -> None:
    box = Viewport(30.0, 50.0, 31.0, 51.0)
    assert box.covers(50.5, 30.5)
    assert box.covers(50.0, 31.0)
    assert not box.covers(49.9, 30.5)
    assert not box.covers(50.5, 31.1)


def test_covers_box_across_antimeridian() -> None:
    box = Viewport(170.0, -10.0, -170.0, 10.0)
    assert box.wraps
    assert box.covers(0.0, 175.0)
    assert box.covers(0.0, -175.0)
    assert not box.covers(0.0, 0.0)


def test_cells_of_small_box() -> None:
    cells = Viewport(30.4, 50.4, 30.6, 50.5).cells(0.25)
    assert cells is not None
    assert sorted(cells) == [(201, 121), (201, 122), (202, 121), (202, 122)]


def test_cells_of_wrapping_box_include_both_sides() -> None:
    cells = Viewport(179.9, 0.0, -179.9, 0.1).cells(0.25)
    assert cells is not None
    columns = {col for _, col in cells}
    assert {719, 720, -720} <= columns


def test_cells_of_huge_box_fall_back_to_scan() -> None:
    whole_world = Viewport(-180, -90, 180, 90)
    cells = whole_world.cells(0.25)
    assert cells is None
    small = Viewport(0, 0, 1, 1).cells(0.25)
    assert small is not None and len(small) <= MAX_VIEWPORT_CELLS
