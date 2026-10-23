from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from geotrack.geo import Latitude, Longitude

MAX_BATCH_REPORTS = 5_000

DeviceId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$"),
]


class PositionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: Latitude
    longitude: Longitude
    timestamp: datetime | None = Field(
        default=None,
        description="Device clock (ISO 8601); naive values are UTC; "
        "receive time is used when omitted",
    )


class DeviceReportIn(PositionIn):
    device_id: DeviceId


class ReportBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[DeviceReportIn] = Field(min_length=1, max_length=MAX_BATCH_REPORTS)


class RejectedReport(BaseModel):
    index: int
    reason: str


class IngestResult(BaseModel):
    queued: int = Field(description="Reports accepted for processing")
    rejected: int = Field(description="Reports refused (validation or overload)")
    errors: list[RejectedReport] = Field(
        default_factory=list, description="Why reports were refused (first 20)"
    )
