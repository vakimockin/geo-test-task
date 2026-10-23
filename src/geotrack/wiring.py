from collections.abc import Sequence
from contextlib import AsyncExitStack

from geotrack.config import Settings
from geotrack.db.engine import make_engine, make_sessions
from geotrack.runtime import Runtime
from geotrack.zones.repository import SqlGeozoneRepository
from geotrack.zones.service import GeozoneService


class _NoBus:
    async def publish_frames(self, items: Sequence[tuple[str, str]]) -> None:
        return None


async def build_runtime(settings: Settings, stack: AsyncExitStack) -> Runtime:
    engine = make_engine(settings)
    stack.push_async_callback(engine.dispose)
    # zone change notices are dropped until the event bus exists
    zones = GeozoneService(
        SqlGeozoneRepository(make_sessions(engine)),
        _NoBus(),
        per_user_limit=settings.max_geozones_per_user,
    )
    return Runtime(settings=settings, zones=zones)
