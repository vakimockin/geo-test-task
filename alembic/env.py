import asyncio
import logging

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from geotrack.config import Settings
from geotrack.db.tables import Base

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", Settings().database_url.replace("%", "%%"))
OWN_TABLES = frozenset(Base.metadata.tables)


def keep(name: str | None, type_: str, parent_names: object) -> bool:
    # the PostGIS image also puts tiger/topology tables on the search path
    return type_ != "table" or name in OWN_TABLES


def apply(connection: Connection | None = None) -> None:
    if connection is None:
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=Base.metadata,
            include_name=keep,
            literal_binds=True,
        )
    else:
        context.configure(
            connection=connection, target_metadata=Base.metadata, include_name=keep
        )
    with context.begin_transaction():
        context.run_migrations()


async def apply_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(apply)
    await engine.dispose()


if context.is_offline_mode():
    apply()
else:
    asyncio.run(apply_online())
