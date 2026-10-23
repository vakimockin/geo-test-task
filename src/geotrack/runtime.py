from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import HTTPConnection

from geotrack.config import Settings
from geotrack.live.hub import Hub
from geotrack.telemetry.intake import Intake
from geotrack.telemetry.pipeline import PositionPipeline
from geotrack.zones.service import GeozoneService

# a readiness probe raises with a short reason when its dependency is down
Probe = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class Runtime:
    """Long-lived components shared by all requests of one process."""

    settings: Settings
    zones: GeozoneService
    intake: Intake
    pipeline: PositionPipeline
    hub: Hub
    probes: Sequence[tuple[str, Probe]] = field(default_factory=list)


def current_runtime(connection: HTTPConnection) -> Runtime:
    return cast(Runtime, connection.app.state.runtime)


RuntimeDep = Annotated[Runtime, Depends(current_runtime)]
