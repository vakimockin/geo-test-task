from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from geotrack.app import create_app
from geotrack.config import Settings
from tests.fakes import StubRuntimeParts, builder_for, stub_runtime


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        max_geozones_per_user=3,
        max_body_bytes=200_000,
        pipeline_capacity=50,
        live_push_interval=0.05,
    )


@pytest.fixture
def parts(settings: Settings) -> StubRuntimeParts:
    return stub_runtime(settings)


@pytest.fixture
def client(settings: Settings, parts: StubRuntimeParts) -> Iterator[TestClient]:
    with TestClient(create_app(settings, builder=builder_for(parts))) as test_client:
        yield test_client
