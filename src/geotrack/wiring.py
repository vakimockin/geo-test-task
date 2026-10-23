from collections.abc import Sequence
from contextlib import AsyncExitStack
from datetime import timedelta

from geotrack.config import Settings
from geotrack.db.engine import make_engine, make_sessions
from geotrack.runtime import Runtime
from geotrack.telemetry.intake import Intake, TimeWindow
from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.pipeline import PositionPipeline
from geotrack.telemetry.store import PositionStore, is_transient
from geotrack.zones.repository import SqlGeozoneRepository
from geotrack.zones.service import GeozoneService


class _NoBus:
    async def publish_frames(self, items: Sequence[tuple[str, str]]) -> None:
        return None


async def build_runtime(settings: Settings, stack: AsyncExitStack) -> Runtime:
    # exit callbacks run in reverse order: the pipeline drains before the engine
    engine = make_engine(settings)
    stack.push_async_callback(engine.dispose)
    store = PositionStore(engine)

    async def persist(batch: list[PositionReport]) -> None:
        await store.apply(batch)

    pipeline = PositionPipeline(
        persist,
        capacity=settings.pipeline_capacity,
        batch_size=settings.pipeline_batch_size,
        max_delay=settings.pipeline_max_delay,
        drain_timeout=settings.pipeline_drain_timeout,
        retryable=is_transient,
    )
    pipeline.start()
    stack.push_async_callback(pipeline.close)

    window = TimeWindow(
        max_future=timedelta(seconds=settings.report_max_future_seconds),
        max_age=timedelta(seconds=settings.report_max_age_seconds),
    )
    # zone change notices are dropped until the event bus exists
    zones = GeozoneService(
        SqlGeozoneRepository(make_sessions(engine)),
        _NoBus(),
        per_user_limit=settings.max_geozones_per_user,
    )
    return Runtime(
        settings=settings,
        zones=zones,
        intake=Intake(pipeline, window),
        pipeline=pipeline,
    )
