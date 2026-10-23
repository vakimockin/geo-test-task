from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from geotrack.config import Settings

APPLICATION_NAME = "geotrack"


def make_engine(settings: Settings) -> AsyncEngine:
    """Explicitly bounded pool; every connection carries a statement timeout."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=1_800,
        connect_args={
            "server_settings": {
                "application_name": APPLICATION_NAME,
                "statement_timeout": str(settings.db_statement_timeout_ms),
            }
        },
    )


def make_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
