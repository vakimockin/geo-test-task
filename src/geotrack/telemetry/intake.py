from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from geotrack.problems import FieldIssue, InvalidInput, Overloaded
from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.schemas import (
    DeviceReportIn,
    IngestResult,
    PositionIn,
    RejectedReport,
)

LISTED_ERRORS = 20
OVERLOADED = "overloaded, retry later"


class ReportQueue(Protocol):
    def offer(self, reports: Sequence[PositionReport]) -> int: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Accepted range of device timestamps relative to the receive time."""

    max_future: timedelta
    max_age: timedelta

    def problem_with(self, reported_at: datetime, now: datetime) -> str | None:
        if reported_at > now + self.max_future:
            return "timestamp is ahead of server time"
        if reported_at < now - self.max_age:
            return "timestamp is too old"
        return None


class Intake:
    """Turns validated request bodies into queued reports (never awaits)."""

    def __init__(
        self,
        queue: ReportQueue,
        window: TimeWindow,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._queue = queue
        self._window = window
        self._clock = clock

    def submit_one(self, device_id: str, body: PositionIn) -> IngestResult:
        now = self._clock()
        report = self._report(device_id, body, now)
        if isinstance(report, str):
            raise InvalidInput(
                report, errors=[FieldIssue(loc="body.timestamp", msg=report)]
            )
        if self._queue.offer([report]) == 0:
            raise Overloaded("ingest queue is full")
        return IngestResult(queued=1, rejected=0)

    def submit_many(self, items: Sequence[DeviceReportIn]) -> IngestResult:
        now = self._clock()
        reports: list[PositionReport] = []
        indexes: list[int] = []
        errors: list[RejectedReport] = []
        for index, item in enumerate(items):
            report = self._report(item.device_id, item, now)
            if isinstance(report, str):
                errors.append(RejectedReport(index=index, reason=report))
            else:
                reports.append(report)
                indexes.append(index)
        if not reports:
            raise InvalidInput(
                "no report in the batch is acceptable",
                errors=[
                    FieldIssue(loc=f"body.positions.{e.index}.timestamp", msg=e.reason)
                    for e in errors[:LISTED_ERRORS]
                ],
            )
        queued = self._queue.offer(reports)
        if queued == 0:
            raise Overloaded("ingest queue is full")
        errors.extend(
            RejectedReport(index=i, reason=OVERLOADED) for i in indexes[queued:]
        )
        errors.sort(key=lambda e: e.index)
        return IngestResult(
            queued=queued,
            rejected=len(items) - queued,
            errors=errors[:LISTED_ERRORS],
        )

    def _report(
        self, device_id: str, body: PositionIn, now: datetime
    ) -> PositionReport | str:
        reported_at = body.timestamp or now
        if reported_at.tzinfo is None:
            reported_at = reported_at.replace(tzinfo=UTC)
        problem = self._window.problem_with(reported_at, now)
        if problem is not None:
            return problem
        return PositionReport(device_id, body.latitude, body.longitude, reported_at)
