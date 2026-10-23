import json

from geotrack.live.cache import PositionCache
from geotrack.live.client import LiveClient
from geotrack.live.hub import Hub
from tests.fakes import FakeSocket, FollowLog


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def session(hub: Hub, user: str) -> LiveClient:
    client = LiveClient(
        user, FakeSocket(), hub.cache, push_interval=1, frame_items=10, backlog=10
    )
    hub.attach(client)
    return client


def queued(client: LiveClient) -> list[str]:
    return list(client.pending)


def make_hub(clock: Clock | None = None) -> tuple[Hub, FollowLog]:
    hub = Hub(PositionCache(cell_degrees=1, ttl=10, clock=clock or Clock()))
    follows = FollowLog()
    hub.subscriptions = follows
    return hub, follows


def test_user_channel_is_followed_while_any_session_is_open() -> None:
    hub, follows = make_hub()
    first, second = session(hub, "alice"), session(hub, "alice")
    session(hub, "bob")
    assert follows.followed == ["alice", "bob"]
    hub.detach(first)
    assert follows.unfollowed == []
    hub.detach(second)
    hub.detach(second)
    assert follows.unfollowed == ["alice"]
    assert hub.session_count() == 1 and hub.users() == ["bob"]


def test_frames_reach_every_session_of_the_user_only() -> None:
    hub, _ = make_hub()
    laptop, phone, other = (
        session(hub, "alice"),
        session(hub, "alice"),
        session(hub, "bob"),
    )
    assert hub.to_user("alice", "A") == 2
    assert queued(laptop) == ["A"] and queued(phone) == ["A"]
    assert queued(other) == []
    assert hub.to_user("nobody", "X") == 0


def test_positions_go_to_the_shared_cache() -> None:
    hub, _ = make_hub()
    hub.positions([("d", 1.0, 1.0, 1)])
    assert len(hub.cache) == 1


def test_sweep_announces_evicted_devices_to_everyone() -> None:
    clock = Clock()
    hub, _ = make_hub(clock)
    alice, bob = session(hub, "alice"), session(hub, "bob")
    hub.positions([("d1", 1.0, 1.0, 1), ("d2", 1.0, 1.0, 1)])
    clock.now = 11
    assert hub.sweep() == 2
    for client in (alice, bob):
        assert json.loads(queued(client)[-1]) == {
            "kind": "gone",
            "device_ids": ["d1", "d2"],
        }
    assert hub.sweep() == 0
