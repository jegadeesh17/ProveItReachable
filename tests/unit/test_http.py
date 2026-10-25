"""Outbound HTTP allowlist (AC-02, AC-06): host, scheme, redirect and byte-cap rules."""

import httpx
import pytest
from fakes.http import RecordingTransport

from pir.http import OutboundBlocked, ResponseTooLarge, make_client

HOST = "api.example.test"


def _client(transport, max_bytes=1000):
    return make_client({HOST}, max_bytes, 5.0, transport=transport)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/x",
        f"https://{HOST}.evil.test/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        f"http://{HOST}/x",
        f"https://{HOST}:8443/x",
        f"https://user@{HOST}/x",
        f"ftp://{HOST}/x",
    ],
)
async def test_ac06_client_refuses_before_sending(url):
    transport = RecordingTransport()
    async with _client(transport) as client:
        with pytest.raises(OutboundBlocked):
            await client.get(url)
    assert transport.requests == []


async def test_ac06_allowed_host_is_served_and_host_match_is_case_insensitive():
    transport = RecordingTransport({f"GET https://{HOST}/ok": httpx.Response(200, json={"a": 1})})
    async with make_client({HOST.upper()}, 1000, 5.0, transport=transport) as client:
        response = await client.get(f"https://{HOST}/ok")
    assert response.json() == {"a": 1}
    assert len(transport.requests) == 1


async def test_ac06_redirects_are_never_followed():
    transport = RecordingTransport(
        {
            f"GET https://{HOST}/old": httpx.Response(
                302, headers={"location": "https://evil.example.test/steal"}
            ),
        }
    )
    async with _client(transport) as client:
        response = await client.get(f"https://{HOST}/old")
    assert response.status_code == 302
    assert transport.urls == [f"GET https://{HOST}/old"]


async def test_ac06_body_over_cap_raises_and_body_at_cap_passes():
    big = httpx.Response(200, content=b"x" * 1001)
    ok = httpx.Response(200, content=b"x" * 1000)
    transport = RecordingTransport({f"GET https://{HOST}/big": big, f"GET https://{HOST}/ok": ok})
    async with _client(transport) as client:
        with pytest.raises(ResponseTooLarge):
            await client.get(f"https://{HOST}/big")
        assert len((await client.get(f"https://{HOST}/ok")).content) == 1000


async def test_ac06_declared_content_length_over_cap_raises():
    transport = RecordingTransport(
        {f"GET https://{HOST}/big": httpx.Response(200, headers={"content-length": "5000"})}
    )
    async with _client(transport) as client:
        with pytest.raises(ResponseTooLarge):
            await client.get(f"https://{HOST}/big")


async def test_ac06_cap_counts_decoded_bytes():
    import gzip

    body = gzip.compress(b"a" * 5000)
    assert len(body) < 1000
    transport = RecordingTransport(
        {
            f"GET https://{HOST}/bomb": httpx.Response(
                200, content=body, headers={"content-encoding": "gzip"}
            )
        }
    )
    async with _client(transport) as client:
        with pytest.raises(ResponseTooLarge):
            await client.get(f"https://{HOST}/bomb")


def test_ac06_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        make_client(set(), 1000, 5.0)
    with pytest.raises(ValueError):
        make_client({HOST}, 0, 5.0)
