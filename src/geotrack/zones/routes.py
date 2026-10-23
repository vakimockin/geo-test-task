from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from geotrack.problems import problem_responses
from geotrack.runtime import RuntimeDep
from geotrack.security import Caller
from geotrack.zones.schemas import GeozoneIn, GeozoneOut, GeozonePatch
from geotrack.zones.service import GeozoneService

router = APIRouter(prefix="/geozones", tags=["geozones"])


def zone_service(runtime: RuntimeDep) -> GeozoneService:
    return runtime.zones


Zones = Annotated[GeozoneService, Depends(zone_service)]

AUTH_ERRORS = problem_responses(
    HTTPStatus.UNAUTHORIZED, HTTPStatus.UNPROCESSABLE_ENTITY
)
ITEM_ERRORS = problem_responses(
    HTTPStatus.UNAUTHORIZED, HTTPStatus.NOT_FOUND, HTTPStatus.UNPROCESSABLE_ENTITY
)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Define a geozone by center and radius",
    responses=problem_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def add_zone(body: GeozoneIn, user: Caller, zones: Zones) -> GeozoneOut:
    return GeozoneOut.model_validate(await zones.create(user, body))


@router.get("", summary="Geozones owned by the caller", responses=AUTH_ERRORS)
async def owned_zones(
    user: Caller,
    zones: Zones,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[GeozoneOut]:
    found = await zones.list(user, limit=limit, offset=offset)
    return [GeozoneOut.model_validate(zone) for zone in found]


@router.get("/{zone_id}", summary="Fetch a geozone", responses=ITEM_ERRORS)
async def one_zone(zone_id: UUID, user: Caller, zones: Zones) -> GeozoneOut:
    return GeozoneOut.model_validate(await zones.get(user, zone_id))


@router.put("/{zone_id}", summary="Replace a geozone", responses=ITEM_ERRORS)
async def replace_zone(
    zone_id: UUID, body: GeozoneIn, user: Caller, zones: Zones
) -> GeozoneOut:
    return GeozoneOut.model_validate(await zones.replace(user, zone_id, body))


@router.patch("/{zone_id}", summary="Modify part of a geozone", responses=ITEM_ERRORS)
async def change_zone(
    zone_id: UUID, body: GeozonePatch, user: Caller, zones: Zones
) -> GeozoneOut:
    return GeozoneOut.model_validate(await zones.patch(user, zone_id, body))


@router.delete(
    "/{zone_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Remove a geozone",
    responses=ITEM_ERRORS,
)
async def drop_zone(zone_id: UUID, user: Caller, zones: Zones) -> Response:
    await zones.delete(user, zone_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)
