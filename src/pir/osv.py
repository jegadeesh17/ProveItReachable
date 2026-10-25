"""OSV client (ARCHITECTURE section 7.3): querybatch, advisory details, 24 h disk cache.

Only IDs matching `OSV_ID_RE` are ever fetched, used in a URL path or used as a cache filename.
A timeout or 5xx after the retries returns an `OsvFailure` for the affected dependencies; it is
never turned into an empty result (AC-26).
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import get_args

import httpx

from pir.config import Settings, load_settings
from pir.http import OSV_HOST, OSV_MAX_BYTES, make_client
from pir.models import Severity
from pir.models.report import Advisory, AdvisoryLink, Dependency

logger = logging.getLogger(__name__)

OSV_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
BATCH_SIZE = 500
CACHE_TTL_S = 24 * 3600
RETRIES = 2
_MAX_PAGE_ROUNDS = 20
_DETAIL_CONCURRENCY = 8
_BACKOFF_S = 0.5
_SEVERITIES = get_args(Severity)
_QUERYBATCH_URL = f"https://{OSV_HOST}/v1/querybatch"
_VULN_URL = f"https://{OSV_HOST}/v1/vulns/"


@dataclass(frozen=True)
class OsvFailure:
    """Why OSV could not be consulted. `message` is plain text, safe to store as `osv_error`."""

    message: str


class _OsvError(Exception):
    """Internal: a request failed for good (after retries). Carries the OsvFailure message."""


def is_valid_osv_id(osv_id: object) -> bool:
    return isinstance(osv_id, str) and OSV_ID_RE.fullmatch(osv_id) is not None


def osv_page_url(osv_id: str) -> str:
    return f"https://osv.dev/vulnerability/{osv_id}"


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_advisory(osv_id: str, data: dict) -> Advisory:
    """Build an Advisory from an OSV vulnerability JSON. Raises ValueError if it is unusable."""
    if data.get("id") != osv_id:
        raise ValueError("advisory id does not match the request")
    aliases = data.get("aliases")
    alias_list = (
        sorted({a for a in aliases if isinstance(a, str)}) if isinstance(aliases, list) else []
    )
    summary = data.get("summary")
    summary = summary[:500] if isinstance(summary, str) and summary else None
    specific = data.get("database_specific")
    severity = specific.get("severity") if isinstance(specific, dict) else None
    severity = severity.lower() if isinstance(severity, str) else "unknown"
    if severity not in _SEVERITIES:
        severity = "unknown"
    fixed: list[str] = []
    for affected in data.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        package = affected.get("package")
        if isinstance(package, dict) and package.get("ecosystem") not in (None, "PyPI"):
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or [] if isinstance(rng, dict) else []:
                value = event.get("fixed") if isinstance(event, dict) else None
                if isinstance(value, str) and value not in fixed:
                    fixed.append(value)
    return Advisory(
        id=osv_id,
        aliases=alias_list,
        summary=summary,
        severity=severity,
        fixed_versions=fixed,
        published=_parse_time(data.get("published")),
        modified=_parse_time(data.get("modified")),
    )


class OsvClient:
    """querybatch + vulns/{id} over `pir.http`. Satisfies the section 7.3 `OsvClient` protocol."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings or load_settings()
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._cache_dir = Path(self._settings.data_dir) / "cache" / "osv"
        self._raw: dict[str, dict] = {}

    # ---- HTTP -------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return make_client(
            {OSV_HOST},
            OSV_MAX_BYTES,
            self._settings.timeout_osv_s,
            headers={"Accept": "application/json", "User-Agent": "ProveItReachable"},
            transport=self._transport,
        )

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kw) -> dict:
        """One JSON request with 2 retries on timeout, transport error, 5xx and 429."""
        last = "OSV request failed"
        for attempt in range(RETRIES + 1):
            if attempt:
                await self._sleep(_BACKOFF_S * 2 ** (attempt - 1))
            try:
                response = await client.request(method, url, **kw)
            except httpx.TimeoutException:
                last = "OSV request timed out"
                continue
            except httpx.HTTPError as exc:
                last = f"OSV request failed: {type(exc).__name__}"
                if isinstance(exc, httpx.TransportError):
                    continue
                raise _OsvError(last) from exc
            status = response.status_code
            if status >= 500 or status == 429:
                last = f"OSV returned HTTP {status}"
                continue
            if status != 200:
                raise _OsvError(f"OSV returned HTTP {status}")
            try:
                data = response.json()
            except ValueError as exc:
                raise _OsvError("OSV returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise _OsvError("OSV returned an unexpected response")
            return data
        raise _OsvError(last)

    # ---- querybatch -------------------------------------------------------------------

    async def _query_chunk(self, client: httpx.AsyncClient, queries: list[dict]) -> list[list[str]]:
        """IDs per query, following next_page_token. Raises _OsvError."""
        found: list[list[str]] = [[] for _ in queries]
        pending = list(range(len(queries)))
        tokens: dict[int, str] = {}
        for _ in range(_MAX_PAGE_ROUNDS):
            body = {
                "queries": [
                    {**queries[i], "page_token": tokens[i]} if i in tokens else queries[i]
                    for i in pending
                ]
            }
            data = await self._request(client, "POST", _QUERYBATCH_URL, json=body)
            results = data.get("results")
            if not isinstance(results, list) or len(results) != len(pending):
                raise _OsvError("OSV returned an unexpected querybatch response")
            next_pending: list[int] = []
            tokens = {}
            for index, result in zip(pending, results, strict=True):
                if not isinstance(result, dict):
                    raise _OsvError("OSV returned an unexpected querybatch response")
                vulns = result.get("vulns") or []
                if not isinstance(vulns, list):
                    raise _OsvError("OSV returned an unexpected querybatch response")
                for vuln in vulns:
                    vuln_id = vuln.get("id") if isinstance(vuln, dict) else None
                    if not isinstance(vuln_id, str):
                        raise _OsvError("OSV returned an unexpected querybatch response")
                    found[index].append(vuln_id)
                token = result.get("next_page_token")
                if token:
                    if not isinstance(token, str):
                        raise _OsvError("OSV returned an unexpected querybatch response")
                    tokens[index] = token
                    next_pending.append(index)
            if not next_pending:
                return found
            pending = next_pending
        raise _OsvError("OSV results had too many pages")

    async def query(self, deps: list[Dependency]) -> dict[str, list[str] | OsvFailure]:
        """Advisory IDs per "name==version"; aliases collapsed to one ID per advisory.

        Unresolved dependencies are not queried. Every queried key maps to IDs or an OsvFailure.
        """
        keys: list[str] = []
        queries: list[dict] = []
        for dep in deps:
            if not dep.resolved or dep.version is None:
                continue
            key = f"{dep.name}=={dep.version}"
            if key in keys:
                continue
            keys.append(key)
            queries.append(
                {"package": {"name": dep.name, "ecosystem": "PyPI"}, "version": dep.version}
            )
        outcome: dict[str, list[str] | OsvFailure] = {}
        ids_by_key: dict[str, list[str]] = {}
        async with self._client() as client:
            for start in range(0, len(queries), BATCH_SIZE):
                chunk_keys = keys[start : start + BATCH_SIZE]
                try:
                    chunk_ids = await self._query_chunk(client, queries[start : start + BATCH_SIZE])
                except _OsvError as exc:
                    for key in chunk_keys:
                        outcome[key] = OsvFailure(str(exc))
                    continue
                for key, ids in zip(chunk_keys, chunk_ids, strict=True):
                    ids_by_key[key] = list(dict.fromkeys(ids))
            advisories = await self._fetch_all(
                client, {i for ids in ids_by_key.values() for i in ids}
            )
        for key in keys:
            if key in outcome:
                continue
            ids = ids_by_key[key]
            bad = next((i for i in ids if not is_valid_osv_id(i)), None)
            if bad is not None:
                outcome[key] = OsvFailure("OSV returned an advisory id that is not accepted")
                continue
            failed = next(
                (advisories[i] for i in ids if isinstance(advisories[i], OsvFailure)), None
            )
            if failed is not None:
                outcome[key] = failed
                continue
            outcome[key] = _dedupe_aliases([advisories[i] for i in ids])  # type: ignore[misc]
        return {key: outcome[key] for key in keys}

    # ---- advisory details -------------------------------------------------------------

    async def _fetch_all(
        self, client: httpx.AsyncClient, ids: set[str]
    ) -> dict[str, Advisory | OsvFailure]:
        gate = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def one(osv_id: str) -> tuple[str, Advisory | OsvFailure]:
            async with gate:
                return osv_id, await self._get_advisory(client, osv_id)

        return dict(await asyncio.gather(*(one(i) for i in sorted(ids))))

    async def get_advisory(self, osv_id: str) -> Advisory | OsvFailure:
        if not is_valid_osv_id(osv_id):
            return OsvFailure("advisory id is not accepted")
        async with self._client() as client:
            return await self._get_advisory(client, osv_id)

    async def _get_advisory(self, client: httpx.AsyncClient, osv_id: str) -> Advisory | OsvFailure:
        if not is_valid_osv_id(osv_id):
            return OsvFailure("advisory id is not accepted")
        data = self._read_cache(osv_id)
        fetched = data is None
        if data is None:
            try:
                data = await self._request(client, "GET", _VULN_URL + osv_id)
            except _OsvError as exc:
                return OsvFailure(str(exc))
        try:
            advisory = _parse_advisory(osv_id, data)
        except ValueError as exc:
            return OsvFailure(f"OSV returned an unusable advisory: {exc}")
        self._raw[osv_id] = data
        if fetched:  # a cache hit must not extend its own 24 h lifetime
            self._write_cache(osv_id, data)
        return advisory

    # ---- cache ------------------------------------------------------------------------

    def _cache_path(self, osv_id: str) -> Path:
        if not is_valid_osv_id(osv_id):
            raise ValueError("advisory id is not accepted")
        return self._cache_dir / f"{osv_id}.json"

    def _read_cache(self, osv_id: str) -> dict | None:
        path = self._cache_path(osv_id)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("osv cache entry unreadable; refetching", extra={"service": "osv"})
            return None
        fetched_at = stored.get("fetched_at") if isinstance(stored, dict) else None
        data = stored.get("data") if isinstance(stored, dict) else None
        if not isinstance(fetched_at, int | float) or not isinstance(data, dict):
            return None
        if self._clock() - fetched_at > CACHE_TTL_S:
            return None
        return data

    def _write_cache(self, osv_id: str, data: dict) -> None:
        path = self._cache_path(osv_id)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"fetched_at": self._clock(), "data": data}), "utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.warning("osv cache write failed", extra={"service": "osv"})
            with contextlib.suppress(OSError):
                tmp.unlink()

    # ---- links ------------------------------------------------------------------------

    def links(self, osv_id: str) -> list[AdvisoryLink]:
        """The osv.dev page plus the advisory's references, URLs exactly as returned."""
        if not is_valid_osv_id(osv_id):
            return []
        links = [AdvisoryLink(url=osv_page_url(osv_id), source="osv", title=None)]
        data = self._raw.get(osv_id)
        if data is None:
            data = self._read_cache(osv_id)
        seen = {links[0].url}
        for ref in (data or {}).get("references") or []:
            url = ref.get("url") if isinstance(ref, dict) else None
            if not isinstance(url, str) or url in seen or len(url) > 2048:
                continue
            if not re.fullmatch(r"https?://\S+", url):
                continue
            seen.add(url)
            links.append(AdvisoryLink(url=url, source="osv", title=None))
        return links


def _dedupe_aliases(advisories: list[Advisory]) -> list[str]:
    """One ID per advisory: IDs that list each other (or a shared alias) collapse together."""
    parent = list(range(len(advisories)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for index, advisory in enumerate(advisories):
        for name in (advisory.id, *advisory.aliases):
            if name in owner:
                parent[find(index)] = find(owner[name])
            else:
                owner[name] = index
    groups: dict[int, list[Advisory]] = {}
    for index, advisory in enumerate(advisories):
        groups.setdefault(find(index), []).append(advisory)
    # keep the entry that carries a severity, then the lowest ID, so the choice is deterministic
    picked = [
        min(group, key=lambda a: (a.severity == "unknown", a.id)).id for group in groups.values()
    ]
    return sorted(picked)
