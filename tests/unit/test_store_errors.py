import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from geotrack.telemetry.store import is_transient


class DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("refused"),
        OSError("Name or service not known"),
        TimeoutError(),
        OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        DBAPIError("SELECT 1", {}, Exception("gone"), connection_invalidated=True),
        DBAPIError("SELECT 1", {}, DriverError("57P03")),
        DBAPIError("SELECT 1", {}, DriverError("57P01")),
        DBAPIError("SELECT 1", {}, DriverError("08006")),
        DBAPIError("SELECT 1", {}, DriverError("53300")),
    ],
)
def test_connection_problems_are_transient(exc: Exception) -> None:
    assert is_transient(exc)


@pytest.mark.parametrize(
    "exc",
    [
        IntegrityError("INSERT", {}, Exception("duplicate key")),
        DBAPIError("SELECT", {}, Exception("syntax error")),
        DBAPIError("INSERT", {}, DriverError("23505")),
        DBAPIError("SELECT", {}, DriverError("57014")),
        ValueError("bad value"),
    ],
)
def test_data_problems_are_not_transient(exc: Exception) -> None:
    assert not is_transient(exc)
