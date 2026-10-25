"""Shared test setup: live-test skip, network guard, frozen clock."""

import ipaddress
import os
import socket
from datetime import UTC, datetime, timedelta

import pytest


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to connect to a non-loopback address."""


def _is_loopback(address: object) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX paths and other non-IP addresses never leave the host
    host = address[0]
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("PIR_LIVE_TESTS") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: set PIR_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _block_external_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.node.get_closest_marker("live") is not None:
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: object) -> None:
        if not _is_loopback(address):
            raise NetworkBlockedError(f"tests may not connect to {address!r}")
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if not _is_loopback(address):
            raise NetworkBlockedError(f"tests may not connect to {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


class FrozenClock:
    """A clock that only moves when the test advances it."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC))
