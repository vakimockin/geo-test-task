from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Path

from geotrack.problems import problem_responses
from geotrack.runtime import RuntimeDep
from geotrack.telemetry.schemas import (
    DeviceId,
    IngestResult,
    PositionIn,
    ReportBatchIn,
)

router = APIRouter(tags=["telemetry"])

INGEST_ERRORS = problem_responses(
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    HTTPStatus.UNPROCESSABLE_ENTITY,
    HTTPStatus.SERVICE_UNAVAILABLE,
)


@router.post(
    "/devices/{device_id}/position",
    status_code=HTTPStatus.ACCEPTED,
    summary="Report the position of one device",
    responses=INGEST_ERRORS,
)
async def report_position(
    device_id: Annotated[DeviceId, Path()], body: PositionIn, runtime: RuntimeDep
) -> IngestResult:
    return runtime.intake.submit_one(device_id, body)


@router.post(
    "/positions",
    status_code=HTTPStatus.ACCEPTED,
    summary="Report positions of many devices",
    description=(
        "Accepts what fits into the ingest queue. Reports refused because of "
        "their timestamp or because the queue is full are counted in "
        "`rejected` and listed (first 20) in `errors`. Answers 422 when no "
        "report is acceptable and 503 with Retry-After when the queue is full."
    ),
    responses=INGEST_ERRORS,
)
async def report_positions(body: ReportBatchIn, runtime: RuntimeDep) -> IngestResult:
    return runtime.intake.submit_many(body.positions)
