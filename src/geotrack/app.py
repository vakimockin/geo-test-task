from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, FastAPI

from geotrack import __version__, health
from geotrack.body_limit import BodyLimitMiddleware
from geotrack.config import Settings
from geotrack.live import routes as live_routes
from geotrack.problems import install_problem_handlers
from geotrack.runtime import Runtime
from geotrack.telemetry import routes as telemetry_routes
from geotrack.wiring import build_runtime
from geotrack.zones import routes as zone_routes

RuntimeBuilder = Callable[[Settings, AsyncExitStack], Awaitable[Runtime]]


def create_app(
    settings: Settings | None = None, *, builder: RuntimeBuilder = build_runtime
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            app.state.runtime = await builder(config, stack)
            yield

    app = FastAPI(
        title="Geotrack",
        version=__version__,
        summary="Live device positions, per-user circular geozones and alerts.",
        lifespan=lifespan,
    )
    install_problem_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_body_bytes)

    api = APIRouter(prefix="/api/v1")
    api.include_router(zone_routes.router)
    api.include_router(telemetry_routes.router)
    app.include_router(api)
    app.include_router(live_routes.router)
    app.include_router(health.router)
    return app
