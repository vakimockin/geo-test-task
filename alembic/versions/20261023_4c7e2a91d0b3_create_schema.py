from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography, Geometry

revision: str = "4c7e2a91d0b3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# superset bbox of the geodesic circle (min WGS 84 meridional radius + 1%)
ENVELOPE_FUNCTION = """
CREATE FUNCTION geozone_envelope(lat double precision,
                                 lon double precision,
                                 radius_m double precision)
RETURNS geometry
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT ST_MakeEnvelope(
        CASE WHEN full_width THEN -180.0 ELSE lon - spread END,
        greatest(lat - arc, -90.0),
        CASE WHEN full_width THEN 180.0 ELSE lon + spread END,
        least(lat + arc, 90.0),
        4326)
    FROM (
        SELECT arc, spread,
               abs(lat) + arc >= 89.0
               OR lon - spread < -180.0
               OR lon + spread > 180.0 AS full_width
        FROM (
            SELECT arc,
                   degrees(asin(least(1.0,
                       sin(radians(arc))
                       / greatest(cos(radians(lat)), 1e-9)))) AS spread
            FROM (SELECT degrees(radius_m * 1.01 / 6335439.0) AS arc) AS a
        ) AS b
    ) AS c
$$
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(ENVELOPE_FUNCTION)

    op.create_table(
        "geozones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("radius_m", sa.Double(), nullable=False),
        sa.Column(
            "center",
            Geography("POINT", srid=4326, spatial_index=False),
            sa.Computed("ST_Point(lon, lat, 4326)::geography", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "envelope",
            Geometry("POLYGON", srid=4326, spatial_index=False),
            sa.Computed("geozone_envelope(lat, lon, radius_m)", persisted=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_geozones"),
        sa.CheckConstraint(
            "lat BETWEEN -90 AND 90", name=op.f("ck_geozones_lat_range")
        ),
        sa.CheckConstraint(
            "lon BETWEEN -180 AND 180", name=op.f("ck_geozones_lon_range")
        ),
        sa.CheckConstraint(
            "radius_m > 0 AND radius_m <= 100000",
            name=op.f("ck_geozones_radius_range"),
        ),
    )
    op.create_index("ix_geozones_owner_created", "geozones", ["owner_id", "created_at"])
    op.create_index(
        "ix_geozones_envelope", "geozones", ["envelope"], postgresql_using="gist"
    )

    op.create_table(
        "device_positions",
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column(
            "position",
            Geography("POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("device_id", name="pk_device_positions"),
    )
    op.create_index(
        "ix_device_positions_position",
        "device_positions",
        ["position"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_table("device_positions")
    op.drop_table("geozones")
    op.execute(
        "DROP FUNCTION geozone_envelope(double precision, double precision, double precision)"
    )
