from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import ARRAY, DateTime, Double, String, bindparam, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from geotrack.telemetry.model import (
    AppliedPosition,
    PositionReport,
    StoreOutcome,
    ZoneHit,
)

# newest report per device -> upsert only if newer -> join applied rows to zones
INGEST_QUERY = """
    WITH incoming AS (
        SELECT DISTINCT ON (r.device_id)
               r.device_id, r.lat, r.lon, r.reported_at
        FROM unnest(:device_ids, :lats, :lons, :reported_ats)
             AS r(device_id, lat, lon, reported_at)
        ORDER BY r.device_id, r.reported_at DESC
    ),
    applied AS (
        INSERT INTO device_positions AS stored
               (device_id, position, reported_at, received_at)
        SELECT device_id, ST_Point(lon, lat, 4326)::geography, reported_at, now()
        FROM incoming
        ORDER BY device_id
        ON CONFLICT (device_id) DO UPDATE
           SET position = EXCLUDED.position,
               reported_at = EXCLUDED.reported_at,
               received_at = EXCLUDED.received_at
           WHERE stored.reported_at < EXCLUDED.reported_at
        RETURNING stored.device_id, stored.position, stored.reported_at
    )
    SELECT a.device_id, i.lat, i.lon, a.reported_at, z.id AS geozone_id, z.owner_id
    FROM applied AS a
    JOIN incoming AS i ON i.device_id = a.device_id
    LEFT JOIN geozones AS z
           ON z.envelope && ST_Point(i.lon, i.lat, 4326)
          AND ST_DWithin(z.center, a.position, z.radius_m)
"""
INGEST_PARAMS = (
    bindparam("device_ids", type_=ARRAY(String)),
    bindparam("lats", type_=ARRAY(Double)),
    bindparam("lons", type_=ARRAY(Double)),
    bindparam("reported_ats", type_=ARRAY(DateTime(timezone=True))),
)
INGEST_SQL = text(INGEST_QUERY).bindparams(*INGEST_PARAMS)

RECENT_SQL = text(
    """
    SELECT device_id,
           ST_Y(position::geometry) AS lat,
           ST_X(position::geometry) AS lon,
           reported_at
    FROM device_positions
    WHERE received_at >= now() - make_interval(secs => :max_age)
    """
)


class PositionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def apply(self, reports: Sequence[PositionReport]) -> StoreOutcome:
        if not reports:
            return StoreOutcome([], [])
        params = {
            "device_ids": [r.device_id for r in reports],
            "lats": [r.lat for r in reports],
            "lons": [r.lon for r in reports],
            "reported_ats": [r.reported_at for r in reports],
        }
        async with self._engine.begin() as conn:
            # positions are rewritten every few seconds, no need to wait for WAL flush
            await conn.execute(text("SET LOCAL synchronous_commit = off"))
            rows = (await conn.execute(INGEST_SQL, params)).all()

        applied: dict[str, AppliedPosition] = {}
        hits: list[ZoneHit] = []
        for device_id, lat, lon, reported_at, geozone_id, owner_id in rows:
            applied.setdefault(
                device_id, AppliedPosition(device_id, lat, lon, reported_at)
            )
            if geozone_id is not None:
                hits.append(
                    ZoneHit(owner_id, geozone_id, device_id, lat, lon, reported_at)
                )
        return StoreOutcome(list(applied.values()), hits)

    async def recent(self, max_age: timedelta) -> list[AppliedPosition]:
        """Positions received within ``max_age``, used to warm the live cache."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                RECENT_SQL, {"max_age": max_age.total_seconds()}
            )
            return [
                AppliedPosition(device_id, lat, lon, reported_at)
                for device_id, lat, lon, reported_at in result
            ]


# connection exceptions, server shutdown/restart, too many connections
TRANSIENT_SQLSTATES = ("08", "57P01", "57P02", "57P03", "53300")


def is_transient(exc: Exception) -> bool:
    """True for connection-level failures worth retrying (not data errors)."""
    if isinstance(exc, (OSError, TimeoutError, OperationalError, InterfaceError)):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    sqlstate = getattr(exc.orig, "sqlstate", None) or ""
    return exc.connection_invalidated or sqlstate.startswith(TRANSIENT_SQLSTATES)
