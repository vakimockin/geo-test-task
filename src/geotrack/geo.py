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
