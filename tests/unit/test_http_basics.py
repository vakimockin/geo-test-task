import pytest

from geotrack.security import parse_user_id


@pytest.mark.parametrize("raw", ["alice", "user.1@corp", "a_b-c", "x" * 64])
def test_valid_user_ids(raw: str) -> None:
    assert parse_user_id(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "   ", "a b", "x*", "x" * 65, "ž"])
def test_invalid_user_ids(raw: str | None) -> None:
    assert parse_user_id(raw) is None
