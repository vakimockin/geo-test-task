from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PositionReport:
    """One device report that passed validation, waiting to be stored."""

    device_id: str
    lat: float
    lon: float
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class AppliedPosition:
    """A report that became the device's latest stored position."""

    device_id: str
    lat: float
    lon: float
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class ZoneHit:
    """An applied position that lies inside one geozone of ``owner_id``."""

    owner_id: str
    geozone_id: UUID
    device_id: str
    lat: float
    lon: float
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class StoreOutcome:
    applied: list[AppliedPosition]
    hits: list[ZoneHit]
