import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"


class FieldIssue(BaseModel):
    loc: str
    msg: str


class ProblemDoc(BaseModel):
    """Shape of every error body (used for the OpenAPI schema)."""

    type: str
    title: str
    status: int
    detail: str
    errors: list[FieldIssue] | None = None


class Problem(Exception):
    """An error that maps to one HTTP status and a stable problem type."""

    status: ClassVar[HTTPStatus] = HTTPStatus.INTERNAL_SERVER_ERROR
    kind: ClassVar[str] = "internal"

    def __init__(
        self,
        detail: str,
        *,
        headers: Mapping[str, str] | None = None,
        errors: list[FieldIssue] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.headers = dict(headers or {})
        self.errors = errors


class Unauthenticated(Problem):
    status = HTTPStatus.UNAUTHORIZED
    kind = "unauthenticated"


class NotFound(Problem):
    status = HTTPStatus.NOT_FOUND
    kind = "not-found"


class Conflict(Problem):
    status = HTTPStatus.CONFLICT
    kind = "conflict"


class InvalidInput(Problem):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    kind = "invalid-input"


class Overloaded(Problem):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    kind = "overloaded"

    def __init__(self, detail: str, *, retry_after: int = 1) -> None:
        super().__init__(detail, headers={"Retry-After": str(retry_after)})


def problem_body(
    status: HTTPStatus | int,
    kind: str,
    detail: str,
    errors: list[FieldIssue] | None = None,
) -> dict[str, Any]:
    code = HTTPStatus(status)
    doc = ProblemDoc(
        type=f"urn:geotrack:problem:{kind}",
        title=code.phrase,
        status=code.value,
        detail=detail,
        errors=errors,
    )
    return doc.model_dump(exclude_none=True)


def problem_response(
    status: HTTPStatus | int,
    kind: str,
    detail: str,
    *,
    errors: list[FieldIssue] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        problem_body(status, kind, detail, errors),
        status_code=int(status),
        headers=dict(headers or {}),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def problem_responses(*statuses: HTTPStatus) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses=`` entries documenting problem bodies."""
    return {
        int(code): {
            "model": ProblemDoc,
            "description": code.phrase,
            "content": {PROBLEM_MEDIA_TYPE: {}},
        }
        for code in statuses
    }


async def _on_problem(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Problem)
    return problem_response(
        exc.status, exc.kind, exc.detail, errors=exc.errors, headers=exc.headers
    )


async def _on_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    issues = [
        FieldIssue(loc=".".join(str(part) for part in err["loc"]), msg=err["msg"])
        for err in exc.errors()
    ]
    return problem_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "invalid-input",
        "request failed validation",
        errors=issues,
    )


async def _on_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code)
    return problem_response(
        exc.status_code, "http", str(detail), headers=exc.headers or {}
    )


async def _on_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return problem_response(
        HTTPStatus.INTERNAL_SERVER_ERROR, "internal", "unexpected server error"
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Problem, _on_problem)
    app.add_exception_handler(RequestValidationError, _on_validation)
    app.add_exception_handler(StarletteHTTPException, _on_http)
    app.add_exception_handler(Exception, _on_unexpected)
