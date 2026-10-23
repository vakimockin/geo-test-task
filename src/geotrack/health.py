import asyncio
from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from geotrack.runtime import Probe, RuntimeDep

router = APIRouter(prefix="/health", tags=["health"])


class Liveness(BaseModel):
    status: Literal["alive"] = "alive"


class ProbeResult(BaseModel):
    ok: bool
    detail: str | None = None


class PipelineInfo(BaseModel):
    depth: int
    accepted: int
    rejected: int
    flushed: int
    failed: int
    retries: int
    lost: int


class LiveInfo(BaseModel):
    sessions: int
    users: int
    devices: int


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    probes: dict[str, ProbeResult]
    pipeline: PipelineInfo
    live: LiveInfo


@router.get("/live", summary="The process is up")
async def liveness() -> Liveness:
    return Liveness()


@router.get(
    "/ready",
    summary="Dependencies are reachable and background work is running",
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def readiness(runtime: RuntimeDep, response: Response) -> Readiness:
    timeout = runtime.settings.ready_check_timeout
    names = [name for name, _ in runtime.probes]
    results = await asyncio.gather(
        *(_run(probe, timeout) for _, probe in runtime.probes)
    )
    probes = dict(zip(names, results))
    ready = all(result.ok for result in results)
    if not ready:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    stats = runtime.pipeline.stats
    hub = runtime.hub
    return Readiness(
        status="ready" if ready else "not_ready",
        probes=probes,
        pipeline=PipelineInfo(
            depth=runtime.pipeline.depth,
            accepted=stats.accepted,
            rejected=stats.rejected,
            flushed=stats.flushed,
            failed=stats.failed,
            retries=stats.retries,
            lost=stats.lost,
        ),
        live=LiveInfo(
            sessions=hub.session_count(),
            users=len(hub.users()),
            devices=len(hub.cache),
        ),
    )


async def _run(probe: Probe, timeout: float) -> ProbeResult:
    try:
        await asyncio.wait_for(probe(), timeout)
    except TimeoutError:
        return ProbeResult(ok=False, detail=f"no answer within {timeout:g}s")
    except Exception as exc:
        return ProbeResult(ok=False, detail=str(exc) or type(exc).__name__)
    return ProbeResult(ok=True)
