import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from geotrack.config import Settings
from geotrack.db.engine import make_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_DATABASE = "geotrack_test"


def scratch_url() -> URL:
    explicit = os.environ.get("GEOTRACK_TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        url = make_url(Settings(_env_file=None).database_url).set(
            database=SCRATCH_DATABASE
        )
    if not (url.database or "").endswith("_test"):
        raise pytest.UsageError("integration tests only run on a *_test database")
    return url


async def create_if_missing(url: URL) -> None:
    server = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    try:
        known = await server.fetchval(
            "SELECT EXISTS (SELECT FROM pg_database WHERE datname = $1)", url.database
        )
        if not known:
            await server.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await server.close()


def migrate(url: str) -> None:
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = scratch_url()
    try:
        asyncio.run(create_if_missing(url))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostGIS is not reachable ({exc})")
    rendered = url.render_as_string(hide_password=False)
    migrate(rendered)
    return rendered


@pytest.fixture
def db_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        pipeline_max_delay=0.02,
        live_push_interval=0.05,
        max_geozones_per_user=3,
    )


@pytest_asyncio.fixture
async def engine(db_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(db_settings)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE geozones, device_positions"))
    yield engine
    await engine.dispose()
