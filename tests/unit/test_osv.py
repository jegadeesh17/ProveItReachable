"""OSV client (AC-10, AC-26): querybatch, alias dedup, retries, id regex, 24 h cache, links."""

import json

import httpx
import pytest
from fakes.http import RecordingTransport, fixture_json, fixture_response, json_response

from pir.config import Settings
from pir.models.report import Dependency
from pir.osv import OSV_ID_RE, OsvClient, OsvFailure

BATCH = "POST https://api.osv.dev/v1/querybatch"
VULN = "GET https://api.osv.dev/v1/vulns/"
GHSA = "GHSA-8q59-q68h-6hv4"
PYSEC = "PYSEC-2021-142"
OTHER = "GHSA-aaaa-bbbb-cccc"


def _dep(name: str, version: str | None = "1.0") -> Dependency:
    resolved = version is not None
    return Dependency(
        name=name,
        source="uv.lock",
        groups=["main"],
        version=version,
        resolved=resolved,
        unresolved_reason=None if resolved else "no version",
        direct=True,
        osv_status="checked" if resolved else "not_queried",
        osv_error=None,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_: float) -> None:
    return None


def _client(tmp_path, transport, clock=None) -> OsvClient:
    return OsvClient(
        Settings(_env_file=None, data_dir=str(tmp_path)),
        transport=transport,
        sleep=_no_sleep,
        clock=clock or Clock(),
    )


def _vuln_routes():
    return {
        VULN + GHSA: fixture_response(200, "osv", f"vuln_{GHSA}.json"),
        VULN + PYSEC: fixture_response(200, "osv", f"vuln_{PYSEC}.json"),
        VULN + OTHER: fixture_response(200, "osv", f"vuln_{OTHER}.json"),
    }


def _cache_files(tmp_path):
    cache = tmp_path / "cache" / "osv"
    return sorted(p.name for p in cache.glob("*")) if cache.exists() else []


# ---- query (AC-10) ---------------------------------------------------------------------


async def test_ac10_body_uses_exact_name_and_resolved_version_and_dedups_aliases(tmp_path):
    transport = RecordingTransport(
        {BATCH: fixture_response(200, "osv", "querybatch_aliases.json"), **_vuln_routes()}
    )
    result = await _client(tmp_path, transport).query(
        [_dep("pyyaml", "5.3.1"), _dep("flask", "2.0")]
    )
    assert transport.json_bodies()[0] == {
        "queries": [
            {"package": {"name": "pyyaml", "ecosystem": "PyPI"}, "version": "5.3.1"},
            {"package": {"name": "flask", "ecosystem": "PyPI"}, "version": "2.0"},
        ]
    }
    # GHSA and PYSEC are the same advisory: one candidate, the one that carries a severity
    assert result == {"pyyaml==5.3.1": [GHSA], "flask==2.0": []}


async def test_ac10_distinct_advisories_each_become_a_candidate(tmp_path):
    transport = RecordingTransport(
        {
            BATCH: json_response(200, {"results": [{"vulns": [{"id": GHSA}, {"id": OTHER}]}]}),
            **_vuln_routes(),
        }
    )
    result = await _client(tmp_path, transport).query([_dep("pyyaml", "5.3.1")])
    assert result == {"pyyaml==5.3.1": sorted([GHSA, OTHER])}


async def test_ac10_unresolved_and_duplicate_dependencies_are_not_queried_twice(tmp_path):
    transport = RecordingTransport({BATCH: json_response(200, {"results": [{}]})})
    deps = [_dep("a", "1.0"), _dep("a", "1.0"), _dep("b", None)]
    result = await _client(tmp_path, transport).query(deps)
    assert result == {"a==1.0": []}
    assert len(transport.json_bodies()[0]["queries"]) == 1


async def test_ac10_nothing_to_query_makes_no_request(tmp_path):
    transport = RecordingTransport()
    assert await _client(tmp_path, transport).query([_dep("b", None)]) == {}
    assert await _client(tmp_path, transport).query([]) == {}
    assert transport.requests == []


async def test_ac10_chunks_of_500_and_next_page_token_followed(tmp_path):
    def batch(request: httpx.Request) -> httpx.Response:
        queries = json.loads(request.content)["queries"]
        results = []
        for q in queries:
            if q.get("page_token") == "tok-2":
                results.append({"vulns": [{"id": OTHER}]})
            elif q["package"]["name"] == "pkg0":
                results.append({"vulns": [{"id": GHSA}], "next_page_token": "tok-2"})
            else:
                results.append({})
        return json_response(200, {"results": results})

    transport = RecordingTransport({BATCH: batch, **_vuln_routes()})
    deps = [_dep(f"pkg{i}", "1.0") for i in range(501)]
    result = await _client(tmp_path, transport).query(deps)
    bodies = transport.json_bodies()
    posts = [b for b in bodies if "queries" in b]
    # chunk 1, then page 2 of pkg0 (follows its token), then chunk 2
    assert [len(b["queries"]) for b in posts] == [500, 1, 1]
    assert posts[1]["queries"][0]["page_token"] == "tok-2"
    assert "page_token" not in posts[2]["queries"][0]
    assert result["pkg0==1.0"] == sorted([GHSA, OTHER])
    assert result["pkg500==1.0"] == []


async def test_ac10_page_loop_is_bounded(tmp_path):
    forever = json_response(200, {"results": [{"vulns": [], "next_page_token": "again"}]})
    transport = RecordingTransport({BATCH: forever})
    result = await _client(tmp_path, transport).query([_dep("a")])
    assert isinstance(result["a==1.0"], OsvFailure)
    assert len(transport.requests) <= 25


# ---- failures (AC-26) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.Response(503)],
    ids=["timeout", "connect", "5xx"],
)
async def test_ac26_failure_after_two_retries_is_osv_failure_never_empty(tmp_path, failure):
    transport = RecordingTransport({BATCH: failure})
    result = await _client(tmp_path, transport).query([_dep("a"), _dep("b")])
    assert len(transport.requests) == 3  # 1 try + 2 retries
    assert set(result) == {"a==1.0", "b==1.0"}
    assert all(isinstance(v, OsvFailure) and v.message for v in result.values())


async def test_ac26_recovers_when_a_retry_succeeds(tmp_path):
    transport = RecordingTransport(
        {BATCH: [httpx.Response(502), json_response(200, {"results": [{}]})]}
    )
    assert await _client(tmp_path, transport).query([_dep("a")]) == {"a==1.0": []}
    assert len(transport.requests) == 2


async def test_ac26_client_error_is_not_retried(tmp_path):
    transport = RecordingTransport({BATCH: httpx.Response(400, json={"message": "bad"})})
    result = await _client(tmp_path, transport).query([_dep("a")])
    assert isinstance(result["a==1.0"], OsvFailure)
    assert len(transport.requests) == 1


async def test_ac26_malformed_batch_response_is_a_failure(tmp_path):
    for payload in ({"results": []}, {"results": "x"}, {"nope": 1}, {"results": [{"vulns": 3}]}):
        transport = RecordingTransport({BATCH: json_response(200, payload)})
        result = await _client(tmp_path, transport).query([_dep("a")])
        assert isinstance(result["a==1.0"], OsvFailure), payload
    transport = RecordingTransport({BATCH: httpx.Response(200, content=b"<html>")})
    assert isinstance((await _client(tmp_path, transport).query([_dep("a")]))["a==1.0"], OsvFailure)


async def test_ac26_one_failed_chunk_does_not_fail_the_other_chunk(tmp_path):
    def batch(request: httpx.Request) -> httpx.Response:
        names = [q["package"]["name"] for q in json.loads(request.content)["queries"]]
        if "pkg0" in names:
            return httpx.Response(503)
        return json_response(200, {"results": [{} for _ in names]})

    transport = RecordingTransport({BATCH: batch})
    result = await _client(tmp_path, transport).query([_dep(f"pkg{i}") for i in range(501)])
    assert isinstance(result["pkg0==1.0"], OsvFailure)
    assert isinstance(result["pkg499==1.0"], OsvFailure)
    assert result["pkg500==1.0"] == []


async def test_ac26_advisory_detail_failure_fails_only_that_dependency(tmp_path):
    transport = RecordingTransport(
        {
            BATCH: json_response(
                200, {"results": [{"vulns": [{"id": GHSA}]}, {"vulns": [{"id": OTHER}]}]}
            ),
            VULN + GHSA: httpx.Response(500),
            VULN + OTHER: fixture_response(200, "osv", f"vuln_{OTHER}.json"),
        }
    )
    result = await _client(tmp_path, transport).query([_dep("a"), _dep("b")])
    assert isinstance(result["a==1.0"], OsvFailure)
    assert result["b==1.0"] == [OTHER]


# ---- id regex (security carry-over) ----------------------------------------------------


@pytest.mark.parametrize(
    "bad_id", ["../x", "..", "a/b", "a\\b", ".hidden", "-x", "", "a" * 101, "a b", "a%2fb", "x\n"]
)
async def test_security_invalid_id_is_neither_fetched_nor_cached(tmp_path, bad_id):
    assert OSV_ID_RE.fullmatch(bad_id) is None
    transport = RecordingTransport()
    result = await _client(tmp_path, transport).get_advisory(bad_id)
    assert isinstance(result, OsvFailure)
    assert transport.requests == []
    assert _cache_files(tmp_path) == []
    assert not (tmp_path / "x.json").exists()


async def test_security_query_returning_an_invalid_id_fails_that_dependency(tmp_path):
    transport = RecordingTransport({BATCH: fixture_response(200, "osv", "querybatch_bad_id.json")})
    result = await _client(tmp_path, transport).query([_dep("a")])
    assert isinstance(result["a==1.0"], OsvFailure)
    assert [r.url.path for r in transport.requests] == ["/v1/querybatch"]  # no vulns request
    assert _cache_files(tmp_path) == []


def test_security_id_regex_accepts_real_ids():
    for good in (GHSA, PYSEC, "CVE-2020-14343", "OSV-2020-1", "a", "A" * 100):
        assert OSV_ID_RE.fullmatch(good)


# ---- advisory details, cache, links ----------------------------------------------------


async def test_ac10_get_advisory_parses_severity_aliases_fixed_and_dates(tmp_path):
    transport = RecordingTransport(_vuln_routes())
    advisory = await _client(tmp_path, transport).get_advisory(GHSA)
    assert advisory.id == GHSA
    assert advisory.aliases == ["CVE-2020-14343", "PYSEC-2021-142"]
    assert advisory.severity == "critical"  # database_specific.severity, lower-cased
    assert advisory.fixed_versions == ["5.4"]
    assert advisory.summary == "Improper Input Validation in PyYAML"
    assert advisory.published.year == 2020 and advisory.modified.year == 2024


async def test_ac10_missing_or_unknown_severity_is_unknown(tmp_path):
    odd = json_response(
        200,
        {**fixture_json("osv", f"vuln_{GHSA}.json"), "database_specific": {"severity": "weird"}},
    )
    transport = RecordingTransport({VULN + GHSA: odd, VULN + PYSEC: _vuln_routes()[VULN + PYSEC]})
    client = _client(tmp_path, transport)
    assert (await client.get_advisory(GHSA)).severity == "unknown"
    assert (await client.get_advisory(PYSEC)).severity == "unknown"


async def test_ac26_get_advisory_failures_are_osv_failure(tmp_path):
    for response in (httpx.Response(404, json={"code": 5}), httpx.Response(503)):
        transport = RecordingTransport({VULN + GHSA: response})
        assert isinstance(await _client(tmp_path, transport).get_advisory(GHSA), OsvFailure)
    transport = RecordingTransport({VULN + GHSA: httpx.ReadTimeout("slow")})
    assert isinstance(await _client(tmp_path, transport).get_advisory(GHSA), OsvFailure)
    assert len(transport.requests) == 3
    assert _cache_files(tmp_path) == []


async def test_security_mismatched_advisory_id_is_not_cached(tmp_path):
    transport = RecordingTransport(
        {VULN + OTHER: fixture_response(200, "osv", f"vuln_{GHSA}.json")}
    )
    assert isinstance(await _client(tmp_path, transport).get_advisory(OTHER), OsvFailure)
    assert _cache_files(tmp_path) == []


async def test_ac10_cache_hit_within_24h_makes_no_request_and_expires_after(tmp_path):
    clock = Clock()
    transport = RecordingTransport(_vuln_routes())
    client = _client(tmp_path, transport, clock)
    first = await client.get_advisory(GHSA)
    assert _cache_files(tmp_path) == [f"{GHSA}.json"]
    assert len(transport.requests) == 1

    clock.now += 23 * 3600  # a hit must not extend the entry's life
    assert await client.get_advisory(GHSA) == first
    assert len(transport.requests) == 1

    clock.now += 2 * 3600  # 25 h after the first fetch
    assert await client.get_advisory(GHSA) == first
    assert len(transport.requests) == 2


async def test_ac10_cache_is_shared_across_clients_and_survives_corruption(tmp_path):
    clock = Clock()
    transport = RecordingTransport(_vuln_routes())
    await _client(tmp_path, transport, clock).get_advisory(GHSA)
    other = RecordingTransport()
    assert (await _client(tmp_path, other, clock).get_advisory(GHSA)).id == GHSA
    assert other.requests == []

    (tmp_path / "cache" / "osv" / f"{GHSA}.json").write_text("{not json", encoding="utf-8")
    assert (await _client(tmp_path, transport, clock).get_advisory(GHSA)).id == GHSA
    assert len(transport.requests) == 2


async def test_ac10_query_uses_cached_details(tmp_path):
    clock = Clock()
    transport = RecordingTransport(_vuln_routes())
    await _client(tmp_path, transport, clock).get_advisory(GHSA)
    batch = RecordingTransport(
        {BATCH: json_response(200, {"results": [{"vulns": [{"id": GHSA}]}]})}
    )
    result = await _client(tmp_path, batch, clock).query([_dep("pyyaml", "5.3.1")])
    assert result == {"pyyaml==5.3.1": [GHSA]}
    assert [r.url.path for r in batch.requests] == ["/v1/querybatch"]


async def test_ac12_links_are_osv_page_plus_references_exactly_as_returned(tmp_path):
    client = _client(tmp_path, RecordingTransport(_vuln_routes()))
    await client.get_advisory(GHSA)
    links = client.links(GHSA)
    assert [link.url for link in links] == [
        f"https://osv.dev/vulnerability/{GHSA}",
        "https://nvd.nist.gov/vuln/detail/CVE-2020-14343",
        "https://github.com/yaml/pyyaml/issues/420?x=1&y=%20z",  # not normalised
        "https://pypi.org/project/PyYAML",
    ]
    assert {link.source for link in links} == {"osv"}


async def test_ac12_links_work_from_the_disk_cache_and_for_unfetched_ids(tmp_path):
    clock = Clock()
    await _client(tmp_path, RecordingTransport(_vuln_routes()), clock).get_advisory(GHSA)
    fresh = _client(tmp_path, RecordingTransport(), clock)
    assert len(fresh.links(GHSA)) == 4
    assert [link.url for link in fresh.links(OTHER)] == [f"https://osv.dev/vulnerability/{OTHER}"]
    assert fresh.links("../x") == []


async def test_ac12_unsafe_reference_urls_are_dropped_not_rewritten(tmp_path):
    vuln = {
        **fixture_json("osv", f"vuln_{GHSA}.json"),
        "references": [
            {"url": "javascript:alert(1)"},
            {"url": "https://ok.example/a b"},
            {"url": "https://ok.example/" + "a" * 2100},
            {"url": "https://ok.example/dup"},
            {"url": "https://ok.example/dup"},
            {"nourl": True},
        ],
    }
    client = _client(tmp_path, RecordingTransport({VULN + GHSA: json_response(200, vuln)}))
    await client.get_advisory(GHSA)
    assert [link.url for link in client.links(GHSA)] == [
        f"https://osv.dev/vulnerability/{GHSA}",
        "https://ok.example/dup",
    ]
