from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geotrack.geo import Latitude, Longitude, RadiusMeters

ZoneName = Field(default="", max_length=100, description="Free-form label")


class GeozoneIn(BaseModel):
    """Full description of a zone (POST and PUT)."""

    model_config = ConfigDict(extra="forbid")

    name: str = ZoneName
    latitude: Latitude
    longitude: Longitude
    radius_m: RadiusMeters


class GeozonePatch(BaseModel):
    """Fields to change (PATCH); a moved center needs both coordinates."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=100)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    radius_m: RadiusMeters | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"fields cannot be null: {', '.join(nulls)}")
        if ("latitude" in self.model_fields_set) != (
            "longitude" in self.model_fields_set
        ):
            raise ValueError("latitude and longitude must be sent together")
        return self

    def changes(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in sorted(self.model_fields_set)}


class GeozoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    latitude: float
    longitude: float
    radius_m: float
    created_at: datetime
    updated_at: datetime
