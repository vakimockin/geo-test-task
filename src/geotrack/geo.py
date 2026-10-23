import math
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

MAX_RADIUS_M = 100_000.0

Latitude = Annotated[
    float, Field(ge=-90, le=90, allow_inf_nan=False, description="Degrees, WGS 84")
]
Longitude = Annotated[
    float, Field(ge=-180, le=180, allow_inf_nan=False, description="Degrees, WGS 84")
]
RadiusMeters = Annotated[
    float,
    Field(
        gt=0,
        le=MAX_RADIUS_M,
        allow_inf_nan=False,
        description=f"Radius in meters, at most {MAX_RADIUS_M:,.0f}",
    ),
]

Cell = tuple[int, int]

# A viewport spanning more cells than this is filtered by scanning instead.
MAX_VIEWPORT_CELLS = 256


def cell_of(lat: float, lon: float, size: float) -> Cell:
    return math.floor(lat / size), math.floor(lon / size)


@dataclass(frozen=True, slots=True)
class Viewport:
    """Map bounds ``[west, south, east, north]``; ``west > east`` wraps ±180."""

    west: float
    south: float
    east: float
    north: float

    @classmethod
    def parse(cls, raw: object) -> "Viewport":
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError("viewport must be [west, south, east, north]")
        if not all(_is_number(v) for v in raw):
            raise ValueError("viewport values must be numbers")
        west, south, east, north = (float(v) for v in raw)
        if not all(math.isfinite(v) for v in (west, south, east, north)):
            raise ValueError("viewport values must be finite")
        if not -90 <= south <= north <= 90:
            raise ValueError("viewport needs -90 <= south <= north <= 90")
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            raise ValueError("viewport longitudes must be within [-180, 180]")
        return cls(west, south, east, north)

    @property
    def wraps(self) -> bool:
        return self.west > self.east

    def covers(self, lat: float, lon: float) -> bool:
        if not self.south <= lat <= self.north:
            return False
        if self.wraps:
            return lon >= self.west or lon <= self.east
        return self.west <= lon <= self.east

    def cells(self, size: float) -> list[Cell] | None:
        """Grid cells intersecting the viewport, or None when there are too many."""
        rows = range(math.floor(self.south / size), math.floor(self.north / size) + 1)
        spans = (
            [(self.west, 180.0), (-180.0, self.east)]
            if self.wraps
            else [(self.west, self.east)]
        )
        columns = [
            col
            for low, high in spans
            for col in range(math.floor(low / size), math.floor(high / size) + 1)
        ]
        if len(rows) * len(columns) > MAX_VIEWPORT_CELLS:
            return None
        return [(row, col) for row in rows for col in columns]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
