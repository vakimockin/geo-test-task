from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from geotrack.problems import InvalidInput, Overloaded
from geotrack.telemetry.intake import Intake, TimeWindow
from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.schemas import DeviceReportIn, PositionIn

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
WINDOW = TimeWindow(max_future=timedelta(seconds=30), max_age=timedelta(hours=1))


class Queue:
    def __init__(self, room: int = 100) -> None:
        self.room = room
        self.items: list[PositionReport] = []

    def offer(self, reports: Sequence[PositionReport]) -> int:
        taken = list(reports[: self.room])
        self.room -= len(taken)
        self.items.extend(taken)
        return len(taken)


def intake(queue: Queue) -> Intake:
    return Intake(queue, WINDOW, clock=lambda: NOW)


def item(device: str, at: datetime | None = None) -> DeviceReportIn:
    return DeviceReportIn(device_id=device, latitude=50.0, longitude=30.0, timestamp=at)


def test_window_reasons() -> None:
    assert WINDOW.problem_with(NOW, NOW) is None
    assert WINDOW.problem_with(NOW + timedelta(seconds=31), NOW) is not None
    assert WINDOW.problem_with(NOW - timedelta(hours=2), NOW) is not None


def test_single_report_defaults_to_receive_time() -> None:
    queue = Queue()
    result = intake(queue).submit_one("unit-1", PositionIn(latitude=1, longitude=2))
    assert (result.queued, result.rejected) == (1, 0)
    assert queue.items == [PositionReport("unit-1", 1, 2, NOW)]


def test_naive_timestamps_are_utc() -> None:
    queue = Queue()
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    intake(queue).submit_one("u", PositionIn(latitude=1, longitude=2, timestamp=naive))
    assert queue.items[0].reported_at == NOW - timedelta(minutes=1)


def test_single_report_with_bad_timestamp_is_invalid() -> None:
    body = PositionIn(latitude=1, longitude=2, timestamp=NOW + timedelta(hours=1))
    with pytest.raises(InvalidInput):
        intake(Queue()).submit_one("u", body)


def test_single_report_when_full_is_overloaded() -> None:
    with pytest.raises(Overloaded) as caught:
        intake(Queue(room=0)).submit_one("u", PositionIn(latitude=1, longitude=2))
    assert caught.value.headers["Retry-After"] == "1"


def test_batch_keeps_good_reports_and_explains_the_rest() -> None:
    queue = Queue()
    result = intake(queue).submit_many(
        [
            item("a"),
            item("b", NOW + timedelta(minutes=10)),
            item("c", NOW - timedelta(days=2)),
        ]
    )
    assert (result.queued, result.rejected) == (1, 2)
    assert [(e.index, e.reason) for e in result.errors] == [
        (1, "timestamp is ahead of server time"),
        (2, "timestamp is too old"),
    ]
    assert [r.device_id for r in queue.items] == ["a"]


def test_batch_partially_accepted_when_queue_fills() -> None:
    result = intake(Queue(room=2)).submit_many(
        [item("a"), item("b", NOW - timedelta(days=2)), item("c"), item("d")]
    )
    assert (result.queued, result.rejected) == (2, 2)
    assert [(e.index, e.reason) for e in result.errors] == [
        (1, "timestamp is too old"),
        (3, "overloaded, retry later"),
    ]


def test_batch_with_nothing_acceptable_is_invalid() -> None:
    with pytest.raises(InvalidInput) as caught:
        intake(Queue()).submit_many([item("a", NOW - timedelta(days=3))])
    assert caught.value.errors and caught.value.errors[0].loc.endswith("0.timestamp")


def test_batch_into_full_queue_is_overloaded() -> None:
    with pytest.raises(Overloaded):
        intake(Queue(room=0)).submit_many([item("a")])


def test_listed_errors_are_capped() -> None:
    old = NOW - timedelta(days=2)
    result = intake(Queue()).submit_many(
        [item("ok")] + [item(f"x{i}", old) for i in range(50)]
    )
    assert result.rejected == 50
    assert len(result.errors) == 20
