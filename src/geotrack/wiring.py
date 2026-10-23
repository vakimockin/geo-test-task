import asyncio
import logging
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from geotrack.config import Settings
from geotrack.db.engine import make_engine, make_sessions
from geotrack.live import frames
from geotrack.live.bus import RedisBus
from geotrack.live.cache import PositionCache
from geotrack.live.hub import Hub
from geotrack.runtime import Runtime
from geotrack.telemetry.intake import Intake, TimeWindow
from geotrack.telemetry.pipeline import PositionPipeline
from geotrack.telemetry.processor import TelemetryProcessor
from geotrack.telemetry.store import PositionStore, is_transient
from geotrack.zones.repository import SqlGeozoneRepository
from geotrack.zones.service import GeozoneService

log = logging.getLogger(__name__)


async def build_runtime(settings: Settings, stack: AsyncExitStack) -> Runtime:
    # exit callbacks run in reverse order: pipeline drain, background tasks, clients
    engine = make_engine(settings)
    stack.push_async_callback(engine.dispose)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        health_check_interval=30,
        # a pooled connection that died with a Redis restart is retried once
        retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=2),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
    stack.push_async_callback(redis.aclose)

    hub = Hub(
        PositionCache(
            cell_degrees=settings.live_grid_cell_degrees,
            ttl=settings.live_device_ttl_seconds,
        )
    )
    bus = RedisBus(redis, hub)
    hub.subscriptions = bus
    store = PositionStore(engine)
    await _warm_cache(hub.cache, store, settings.live_device_ttl_seconds)

    _background(stack, bus.run(), "redis-bus")
    _background(
        stack,
        hub.sweep_forever(max(1.0, settings.live_device_ttl_seconds / 4)),
        "live-sweeper",
    )

    pipeline = PositionPipeline(
        TelemetryProcessor(store, bus),
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
    zones = GeozoneService(
        SqlGeozoneRepository(make_sessions(engine)),
        bus,
        per_user_limit=settings.max_geozones_per_user,
    )
    return Runtime(
        settings=settings,
        zones=zones,
        intake=Intake(pipeline, window),
        pipeline=pipeline,
        hub=hub,
    )


def _background(
    stack: AsyncExitStack, work: Coroutine[Any, Any, None], name: str
) -> None:
    task = asyncio.create_task(work, name=name)

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    stack.push_async_callback(stop)


async def _warm_cache(cache: PositionCache, store: PositionStore, ttl: float) -> None:
    """Start with recent positions so new sessions see devices immediately."""
    try:
        recent = await store.recent(timedelta(seconds=ttl))
    except Exception as exc:
        log.warning("live cache starts empty, database not reachable: %s", exc)
        return
    cache.apply(
        (p.device_id, p.lat, p.lon, frames.epoch_ms(p.reported_at)) for p in recent
    )
    log.info("live cache warmed with %d devices", len(recent))
