from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Configuration from ``GEOTRACK_*`` variables; every number is bounded."""

    model_config = SettingsConfigDict(
        env_prefix="GEOTRACK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: str = "postgresql+asyncpg://geo:geo@localhost:54329/geo"
    redis_url: str = "redis://localhost:6379/0"

    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65_535)
    log_level: LogLevel = "INFO"
    shutdown_grace_seconds: int = Field(15, ge=1, le=300)
    max_body_bytes: int = Field(1_048_576, ge=1_024, le=64 * 1_048_576)

    db_pool_size: int = Field(10, ge=1, le=200)
    db_max_overflow: int = Field(10, ge=0, le=200)
    db_pool_timeout: float = Field(10.0, gt=0, le=120)
    db_statement_timeout_ms: int = Field(5_000, ge=100, le=600_000)

    pipeline_capacity: int = Field(60_000, ge=1, le=5_000_000)
    pipeline_batch_size: int = Field(2_000, ge=1, le=50_000)
    pipeline_max_delay: float = Field(0.1, gt=0, le=10)
    pipeline_drain_timeout: float = Field(10.0, ge=0, le=300)

    report_max_future_seconds: float = Field(30.0, ge=0, le=86_400)
    report_max_age_seconds: float = Field(21_600.0, gt=0, le=30 * 86_400)

    max_geozones_per_user: int = Field(200, ge=1, le=100_000)

    live_push_interval: float = Field(1.0, gt=0, le=60)
    live_frame_items: int = Field(2_000, ge=1, le=50_000)
    live_control_backlog: int = Field(256, ge=1, le=100_000)
    live_device_ttl_seconds: float = Field(120.0, gt=0, le=86_400)
    live_grid_cell_degrees: float = Field(0.25, gt=0, le=45)

    ready_check_timeout: float = Field(2.0, gt=0, le=30)

    @field_validator("database_url")
    @classmethod
    def _asyncpg_only(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg driver")
        return value

    @field_validator("redis_url")
    @classmethod
    def _redis_scheme(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("redis_url must be a redis://, rediss:// or unix:// URL")
        return value
