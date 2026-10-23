import pytest
from pydantic import ValidationError

from geotrack.config import Settings


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_are_usable_for_local_development() -> None:
    settings = make()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.db_pool_size + settings.db_max_overflow <= 100
    assert settings.pipeline_batch_size <= settings.pipeline_capacity


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOTRACK_DB_POOL_SIZE", "7")
    monkeypatch.setenv("GEOTRACK_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DB_POOL_SIZE", "99")
    settings = make()
    assert settings.db_pool_size == 7
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "field, value",
    [
        ("db_pool_size", 0),
        ("pipeline_capacity", 0),
        ("pipeline_max_delay", 0),
        ("live_push_interval", -1),
        ("max_body_bytes", 10),
        ("port", 70_000),
    ],
)
def test_rejects_out_of_range_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make(**{field: value})


def test_requires_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make(database_url="postgresql://geo:geo@localhost/geo")


def test_requires_redis_scheme() -> None:
    with pytest.raises(ValidationError, match="redis"):
        make(redis_url="http://localhost:6379")


def test_is_immutable() -> None:
    settings = make()
    with pytest.raises(ValidationError):
        settings.port = 1  # type: ignore[misc]
