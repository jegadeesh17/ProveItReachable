"""RateLimiter, client_key and ClientHasher (ARCHITECTURE 7.8 and 11.2; findings 5 and 8; AC-50)."""

from datetime import UTC, datetime, timedelta

import pytest

from pir.config import Settings
from pir.ratelimit import Allowed, ClientHasher, Limited, RateLimiter, client_key

T0 = datetime(2026, 10, 26, 10, 0, 0, tzinfo=UTC)


def test_ac50_fourth_request_in_an_hour_is_limited_with_the_wait():
    limiter = RateLimiter()
    for minute in (0, 10, 20):
        assert limiter.check_and_consume("a", T0 + timedelta(minutes=minute)) == Allowed()

    verdict = limiter.check_and_consume("a", T0 + timedelta(minutes=30))

    assert verdict == Limited(retry_after_s=30 * 60, scope="hour")  # the first hit leaves at +60


def test_the_window_slides_so_the_client_can_start_again():
    limiter = RateLimiter()
    for minute in (0, 1, 2):
        limiter.check_and_consume("a", T0 + timedelta(minutes=minute))

    assert isinstance(limiter.check_and_consume("a", T0 + timedelta(minutes=59)), Limited)
    assert limiter.check_and_consume("a", T0 + timedelta(minutes=61)) == Allowed()


def test_a_refused_request_is_not_counted():
    limiter = RateLimiter(per_hour=1, per_day=10)
    limiter.check_and_consume("a", T0)
    for _ in range(5):
        assert isinstance(limiter.check_and_consume("a", T0 + timedelta(minutes=5)), Limited)

    # Only the first request is on record: it leaves the window at +60 minutes.
    assert limiter.check_and_consume("a", T0 + timedelta(minutes=61)) == Allowed()


def test_ten_per_client_per_day():
    limiter = RateLimiter(per_hour=100, per_day=10, global_per_day=1000)
    for i in range(10):
        assert limiter.check_and_consume("a", T0 + timedelta(hours=i * 2 / 10)) == Allowed()

    verdict = limiter.check_and_consume("a", T0 + timedelta(hours=3))

    assert isinstance(verdict, Limited) and verdict.scope == "day"
    assert verdict.retry_after_s == 21 * 3600  # the oldest hit leaves 24 h after T0


def test_clients_are_limited_separately():
    limiter = RateLimiter(per_hour=1)
    assert limiter.check_and_consume("a", T0) == Allowed()
    assert isinstance(limiter.check_and_consume("a", T0), Limited)
    assert limiter.check_and_consume("b", T0) == Allowed()


def test_global_limit_per_utc_day_resets_at_midnight_and_does_not_charge_the_client():
    limiter = RateLimiter(per_hour=100, per_day=100, global_per_day=10)
    late = datetime(2026, 10, 26, 23, 0, 0, tzinfo=UTC)
    for i in range(10):
        assert limiter.check_and_consume(f"c{i}", late) == Allowed()

    verdict = limiter.check_and_consume("c-new", late)
    assert verdict == Limited(retry_after_s=3600, scope="global")

    midnight = late + timedelta(hours=1, seconds=1)
    assert limiter.check_and_consume("c-new", midnight) == Allowed()


def test_zero_limits_refuse_everything():
    assert isinstance(RateLimiter(per_hour=0).check_and_consume("a", T0), Limited)
    assert isinstance(RateLimiter(per_day=0).check_and_consume("a", T0), Limited)
    assert isinstance(RateLimiter(global_per_day=0).check_and_consume("a", T0), Limited)


def test_state_is_pruned_of_idle_clients():
    limiter = RateLimiter()
    limiter.check_and_consume("a", T0)
    limiter.check_and_consume("b", T0 + timedelta(days=2))

    assert set(limiter._hits) == {"b"}


def test_from_settings_uses_the_configured_limits():
    settings = Settings(
        _env_file=None,
        rate_limit_per_client_hour=1,
        rate_limit_per_client_day=2,
        rate_limit_global_day=3,
    )
    limiter = RateLimiter.from_settings(settings)

    assert limiter.check_and_consume("a", T0) == Allowed()
    assert isinstance(limiter.check_and_consume("a", T0), Limited)


# ---- client_key (finding 8) ----------------------------------------------------------------


def test_without_trusted_hops_the_connection_address_is_the_key():
    assert client_key("10.0.0.1", "1.1.1.1, 2.2.2.2", 0) == "10.0.0.1"


def test_with_one_hop_the_last_entry_is_used_not_the_leftmost():
    assert client_key("10.0.0.1", "6.6.6.6, 9.9.9.9", 1) == "9.9.9.9"


def test_with_two_hops_the_second_from_the_right_is_used():
    assert client_key("10.0.0.1", "6.6.6.6, 9.9.9.9, 10.0.0.5", 2) == "9.9.9.9"


def test_fewer_entries_than_hops_uses_the_connection_address():
    assert client_key("10.0.0.1", "6.6.6.6", 2) == "10.0.0.1"  # a spoofed leftmost is ignored


@pytest.mark.parametrize("header", [None, "", "not-an-ip", "1.1.1.1, ", "1.1.1.1, <script>"])
def test_missing_or_malformed_header_uses_the_connection_address(header):
    assert client_key("10.0.0.1", header, 1) == "10.0.0.1"


def test_ipv6_entries_are_normalised():
    assert client_key("10.0.0.1", "x, 2001:DB8:0:0:0:0:0:1", 1) == "2001:db8::1"


def test_no_connection_address_gives_a_fixed_key():
    assert client_key(None, None, 0) == "unknown"


# ---- ClientHasher --------------------------------------------------------------------------


def test_hash_is_stable_salted_and_hides_the_key():
    a, b = ClientHasher(), ClientHasher()

    assert a.hash("203.0.113.9") == a.hash("203.0.113.9")
    assert a.hash("203.0.113.9") != a.hash("203.0.113.10")
    assert a.hash("203.0.113.9") != b.hash("203.0.113.9")  # a different salt per process
    assert "203.0.113.9" not in a.hash("203.0.113.9")
    assert ClientHasher("s").hash("k") == ClientHasher("s").hash("k")
