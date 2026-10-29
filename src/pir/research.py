"""Search client and advisory researcher (ARCHITECTURE section 7.4, ADR-0031).

One Tavily search per advisory, then one Nemotron JSON-mode extraction over the OSV summary and the
snippets. A symbol survives only if it has the shape of a dotted Python path and its last segment
appears word for word in the supplied text; evidence URLs survive only if the search returned
them. Links are the URL strings exactly as OSV and Tavily returned them. `research` never raises:
a failure is reported in `AdvisoryResearch.failure`. Results are cached per advisory ID for 7
days, under IDs that pass `OSV_ID_RE` only (the ID is the filename).
"""

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import ValidationError

from pir.config import Settings, load_settings, require
from pir.fix_commit import changed_functions, commit_refs, fetch_diff
from pir.http import GITHUB_HOST, GITHUB_MAX_BYTES, MIB, OutboundBlocked, make_client
from pir.llm import LLMClient, LLMError
from pir.models import ReasonCode
from pir.models.report import NOT_IDENTIFIED_PREFIX, Advisory, AdvisoryLink
from pir.models.research import AdvisoryResearch, SearchResult
from pir.osv import is_valid_osv_id, osv_page_url
from pir.reasoning.equivalences import EQUIVALENCES
from pir.reasoning.prompts import research_messages, research_text
from pir.spend import ScanBudget, SpendDenied
from pir.tracing import advisory_run, tool_run

logger = logging.getLogger(__name__)

TAVILY_HOST = "api.tavily.com"
TAVILY_URL = f"https://{TAVILY_HOST}/search"
TAVILY_MAX_BYTES = 2 * MIB
CACHE_TTL_S = 7 * 24 * 3600
RETRIES = 1
_BACKOFF_S = 0.5
_SNIPPET_MAX = 2000
_URL_MAX = 2048
_TITLE_MAX = 200
_MAX_SYMBOLS = 10
_SYMBOL_MAX = 200
_SYMBOL_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", re.ASCII)
_HTTP_URL_RE = re.compile(r"https?://\S+")
_EXTRACTION_MAX_TOKENS_FLOOR = 300
_IDENT_RE = re.compile(r"[A-Za-z_]\w*", re.ASCII)
# PyPI name (PEP 503 form) -> the module the code imports it by, where the two differ. Any other
# package is imported by its own name with dashes written as underscores.
_IMPORT_NAMES = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "scikit-learn": "sklearn",
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
    "pyjwt": "jwt",
    "pycryptodome": "Crypto",
    "pycryptodomex": "Cryptodome",
    "python-jose": "jose",
    "protobuf": "google",
}

Depth = Literal["basic", "advanced"]


class SearchFailed(Exception):
    """A search failed for good (after the retry). The message never carries a secret."""

    def __init__(self, kind: Literal["timeout", "http", "bad_response"], message: str) -> None:
        super().__init__(message)
        self.kind = kind


class SearchClient(Protocol):
    async def search(
        self, query: str, *, max_results: int, depth: Depth, timeout_s: float
    ) -> list[SearchResult]: ...


def _clean_results(data: object, max_results: int) -> list[SearchResult]:
    """Read only `results[].url`, `title`, `content`. URLs are kept exactly as returned."""
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchFailed("bad_response", "Tavily returned an unexpected response")
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url, title, content = item.get("url"), item.get("title"), item.get("content")
        if not isinstance(url, str) or len(url) > _URL_MAX or not _HTTP_URL_RE.fullmatch(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                url=url,
                title=title if isinstance(title, str) else None,
                content=content[:_SNIPPET_MAX] if isinstance(content, str) else "",
            )
        )
        if len(results) >= max_results:
            break
    return results


class TavilySearchClient:
    """`POST https://api.tavily.com/search` with a Bearer key. Satisfies `SearchClient`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings or load_settings()
        require(self._settings, ["TAVILY_API_KEY"])
        assert self._settings.tavily_api_key is not None  # require() checked it
        self._key = self._settings.tavily_api_key
        self._transport = transport
        self._sleep = sleep

    async def search(
        self, query: str, *, max_results: int, depth: Depth, timeout_s: float
    ) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        if depth not in ("basic", "advanced"):
            raise ValueError("depth must be 'basic' or 'advanced'")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        body = {"query": query, "search_depth": depth, "max_results": max_results}
        headers = {
            "Authorization": f"Bearer {self._key.get_secret_value()}",
            "User-Agent": "ProveItReachable",
        }
        last = SearchFailed("http", "Tavily request failed")
        async with make_client(
            {TAVILY_HOST}, TAVILY_MAX_BYTES, timeout_s, transport=self._transport
        ) as client:
            for attempt in range(RETRIES + 1):
                if attempt:
                    await self._sleep(_BACKOFF_S)
                try:
                    async with asyncio.timeout(timeout_s):
                        response = await client.post(
                            TAVILY_URL, json=body, headers=headers, timeout=timeout_s
                        )
                except (TimeoutError, httpx.TimeoutException):
                    last = SearchFailed("timeout", "Tavily request timed out")
                    continue
                except httpx.TransportError as exc:
                    last = SearchFailed("http", f"Tavily request failed: {type(exc).__name__}")
                    continue
                except (httpx.HTTPError, OutboundBlocked) as exc:
                    raise SearchFailed(
                        "http", f"Tavily request failed: {type(exc).__name__}"
                    ) from exc
                status = response.status_code
                if status >= 500 or status == 429:
                    last = SearchFailed("http", f"Tavily returned HTTP {status}")
                    continue
                if status != 200:
                    raise SearchFailed("http", f"Tavily returned HTTP {status}")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise SearchFailed("bad_response", "Tavily returned invalid JSON") from exc
                return _clean_results(data, max_results)
        raise last


def _grounded(symbol: str, corpus: str) -> bool:
    if len(symbol) > _SYMBOL_MAX or not _SYMBOL_RE.fullmatch(symbol):
        return False
    last = symbol.rsplit(".", 1)[-1]
    return re.search(rf"(?<!\w){re.escape(last)}(?!\w)", corpus) is not None


def _import_root(package: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", package).lower()
    return _IMPORT_NAMES.get(normalized, normalized.replace("-", "_"))


def _provided_by(symbol: str, root: str) -> bool:
    """Whether the symbol's top-level module is the import name of the package.

    `shutil.copy` is not a function of `virtualenv`, whatever the advisory page says next to it,
    so a call to it in repository code says nothing about that package. Standard-library modules
    fail this test like any other foreign module; a package that really provides one (its import
    name is that module) passes.
    """
    return symbol.split(".", 1)[0] == root


def _foreign_note(rejected: list[str], root: str) -> str:
    names = ", ".join(f"`{name}`" for name in rejected[:3])
    return f"{names} named in the research is not in the module `{root}` that the package provides"


def _qualify(symbol: str, root: str) -> str:
    """`root.` in front of a name that leaves out the module (`full_load`, `FullLoader.full_load`).

    The model often names the function as the advisory text does, without the module the code
    imports the package by, and then neither the index targets nor the symbol searches can find
    it. A lower-case dotted name is a module path and stays as written.
    """
    first = symbol.split(".", 1)[0]
    if first == root or not _IDENT_RE.fullmatch(first):
        return symbol
    if "." not in symbol or first[0].isupper():
        return f"{root}.{symbol}"
    return symbol


def _candidates(named: str, root: str) -> list[str]:
    """The symbols to check for one name the model drew, most useful first.

    A class-qualified method (`yaml.FullLoader.full_load`) is also called as a module-level function
    (`yaml.full_load`), and that is the call repository code writes, so it goes first. The extra
    name matches only a call that exists, so it adds no false positive of its own. One exception: a
    function whose meaning depends on its arguments (`yaml.load`, the equivalence table's calls) is
    never added, because a bare `yaml.load` would also match its safe calls (ADR-0037).
    """
    symbol = _qualify(named, root)
    parts = symbol.split(".")
    if len(parts) == 3 and parts[0] == root and parts[1][:1].isupper() and parts[2]:
        module_level = f"{root}.{parts[2]}"
        if all(row.call != module_level for row in EQUIVALENCES):
            return [module_level, symbol]
    return [symbol]


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


class AdvisoryResearcher:
    """Never raises; a failure is reported in `AdvisoryResearch.failure`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        search: SearchClient,
        llm: LLMClient,
        github_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        bill_search: bool = True,
    ) -> None:
        """`bill_search=False` is for a search client answered from recordings: the call still
        needs budget headroom, but it costs nothing and the meter is charged 0."""
        self._settings = settings or load_settings()
        self._search = search
        self._llm = llm
        self._github_client = github_client
        self._transport = transport
        self._clock = clock
        self._bill_search = bill_search
        self._cache_dir = Path(self._settings.data_dir) / "cache" / "research"
        self._diff_cache_dir = Path(self._settings.data_dir) / "cache" / "diffs"

    async def research(
        self, advisory: Advisory, package: str, osv_links: list[AdvisoryLink], budget: ScanBudget
    ) -> AdvisoryResearch:
        with advisory_run(advisory.id, package) as span:
            research = await self._research(advisory, package, osv_links, budget)
            span.add_metadata(from_cache=research.from_cache)
            span.set_outputs(
                {
                    "from_cache": research.from_cache,
                    "failure": research.failure,
                    "symbols": len(research.symbols),
                }
            )
        return research

    async def _research(
        self, advisory: Advisory, package: str, osv_links: list[AdvisoryLink], budget: ScanBudget
    ) -> AdvisoryResearch:
        stage: ReasonCode = "tavily_failed"
        osv = self._osv_links(advisory.id, osv_links)
        results: list[SearchResult] = []
        try:
            cached = self._read_cache(advisory.id, osv, package)
            if cached is not None:
                return cached
            results = await self._run_search(advisory, package, budget)
            stage = "nebius_failed"
            symbols, evidence, rejected = await self._extract(advisory, package, results, budget)
            if not symbols:
                symbols, commit_url = await self._fallback_fix_commit(package, osv)
                if symbols and commit_url:
                    evidence = [commit_url]
        except _StageFailed as exc:
            logger.warning("research failed: %s", exc.failure, extra={"service": "research"})
            return self._result(advisory.id, osv, results, [], [], failure=exc.failure)
        except Exception:
            logger.exception("research failed unexpectedly", extra={"service": "research"})
            return self._result(advisory.id, osv, results, [], [], failure=stage)
        research = self._result(
            advisory.id,
            osv,
            results,
            symbols,
            evidence,
            foreign=_foreign_note(rejected, _import_root(package)) if rejected else None,
        )
        self._write_cache(advisory.id, research, results)
        return research

    # ---- steps ------------------------------------------------------------------------

    async def _run_search(
        self, advisory: Advisory, package: str, budget: ScanBudget
    ) -> list[SearchResult]:
        depth = self._settings.tavily_search_depth
        credits = 1 if depth == "basic" else 2
        decision = budget.try_spend("tavily", credits * self._settings.tavily_price_per_credit_usd)
        if isinstance(decision, SpendDenied):
            raise _StageFailed("tavily_failed")
        parts = [
            package,
            advisory.id,
            advisory.aliases[0] if advisory.aliases else "",
            "vulnerable function",
        ]
        query = " ".join(part for part in parts if part)
        try:
            with tool_run(
                "tavily_search",
                {"query": query, "max_results": self._settings.tavily_max_results, "depth": depth},
            ) as span:
                budget.count("tavily_searches")  # a request sent; cached answers never reach here
                results = await self._search.search(
                    query,
                    max_results=self._settings.tavily_max_results,
                    depth=depth,
                    timeout_s=self._settings.timeout_tavily_s,
                )
                span.set_outputs(
                    {"result_count": len(results), "urls": [result.url for result in results]}
                )
        except SearchFailed as exc:
            # A timeout may still have been billed: keep the hold. Other failures cost nothing.
            billed = self._bill_search and exc.kind == "timeout"
            budget.record(decision, decision.amount_usd if billed else 0.0, service="tavily")
            raise _StageFailed("tavily_failed") from exc
        except BaseException:
            budget.record(
                decision, decision.amount_usd if self._bill_search else 0.0, service="tavily"
            )
            raise
        budget.record(decision, decision.amount_usd if self._bill_search else 0.0, service="tavily")
        return results

    async def _extract(
        self, advisory: Advisory, package: str, results: list[SearchResult], budget: ScanBudget
    ) -> tuple[list[str], list[str], list[str]]:
        """Grounded symbols of the package, the evidence URLs, and the symbols of other modules."""
        messages = research_messages(package, advisory.id, advisory.summary, results)
        try:
            reply = await self._llm.chat(
                messages,
                json_mode=True,
                max_tokens=max(self._settings.max_completion_tokens, _EXTRACTION_MAX_TOKENS_FLOOR),
                timeout_s=self._settings.timeout_nebius_s,
                budget=budget,
            )
        except LLMError as exc:
            raise _StageFailed("nebius_failed") from exc
        try:
            data = json.loads(reply.content or "")
        except ValueError as exc:
            raise _StageFailed("nebius_failed") from exc
        if not isinstance(data, dict):
            raise _StageFailed("nebius_failed")

        corpus = research_text(advisory.summary, results)
        symbols: list[str] = []
        rejected: list[str] = []
        root = _import_root(package)
        for named in _string_list(data.get("symbols")):
            for symbol in _candidates(named, root):
                if symbol in symbols or symbol in rejected or not _grounded(symbol, corpus):
                    continue
                if _provided_by(symbol, root):
                    symbols.append(symbol)
                else:
                    rejected.append(symbol)
            if len(symbols) >= _MAX_SYMBOLS:
                symbols = symbols[:_MAX_SYMBOLS]
                break
        returned = {result.url for result in results}
        evidence: list[str] = []
        if symbols:
            for url in _string_list(data.get("evidence_urls")):
                if url in returned and url not in evidence:
                    evidence.append(url)
        return symbols, evidence, rejected

    async def _fallback_fix_commit(
        self, package: str, osv: list[AdvisoryLink]
    ) -> tuple[list[str], str | None]:
        refs = commit_refs(osv)
        if not refs:
            return [], None

        root = _import_root(package)
        for owner, repo, sha in refs:
            diff_text = await self._fetch_diff(owner, repo, sha)
            if not diff_text:
                continue
            funcs = changed_functions(diff_text, root)
            if not funcs:
                continue

            symbols: list[str] = []
            for named in funcs:
                for symbol in _candidates(named, root):
                    if symbol not in symbols:
                        symbols.append(symbol)
                if len(symbols) >= _MAX_SYMBOLS:
                    symbols = symbols[:_MAX_SYMBOLS]
                    break

            if not symbols:
                continue

            commit_url = None
            sha_lower = sha.lower()
            for link in osv:
                if f"/commit/{sha_lower}" in link.url.lower():
                    commit_url = link.url
                    break
            if not commit_url:
                commit_url = f"https://github.com/{owner}/{repo}/commit/{sha_lower}"
                osv.append(AdvisoryLink(url=commit_url, source="osv", title="Fix commit"))

            return symbols, commit_url

        return [], None

    async def _fetch_diff(self, owner: str, repo: str, sha: str) -> str:
        if self._github_client is not None:
            return await fetch_diff(
                self._github_client, owner, repo, sha, cache_dir=self._diff_cache_dir
            )
        headers = {
            "Accept": "application/vnd.github.diff",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ProveItReachable",
        }
        if (
            self._settings.github_token is not None
            and self._settings.github_token.get_secret_value()
        ):
            headers["Authorization"] = f"Bearer {self._settings.github_token.get_secret_value()}"
        try:
            async with make_client(
                {GITHUB_HOST},
                GITHUB_MAX_BYTES,
                self._settings.timeout_github_s,
                headers=headers,
                transport=self._transport,
            ) as client:
                return await fetch_diff(client, owner, repo, sha, cache_dir=self._diff_cache_dir)
        except Exception as exc:
            logger.warning(
                "failed to create github client or fetch diff: %s",
                exc,
                extra={"service": "research"},
            )
            return ""

    # ---- result -----------------------------------------------------------------------

    @staticmethod
    def _osv_links(advisory_id: str, osv_links: list[AdvisoryLink]) -> list[AdvisoryLink]:
        """OSV references as given, plus the osv.dev page if it is missing."""
        links: list[AdvisoryLink] = []
        seen: set[str] = set()
        for link in osv_links:
            if link.url not in seen:
                seen.add(link.url)
                links.append(link)
        if is_valid_osv_id(advisory_id) and osv_page_url(advisory_id) not in seen:
            links.insert(0, AdvisoryLink(url=osv_page_url(advisory_id), source="osv", title=None))
        return links

    @staticmethod
    def _tavily_links(results: list[SearchResult], taken: set[str]) -> list[AdvisoryLink]:
        links: list[AdvisoryLink] = []
        for result in results:
            if result.url in taken:
                continue
            taken.add(result.url)
            title = (result.title or "")[:_TITLE_MAX] or None
            try:
                links.append(AdvisoryLink(url=result.url, source="tavily", title=title))
            except ValidationError:
                continue
        return links

    def _result(
        self,
        advisory_id: str,
        osv: list[AdvisoryLink],
        results: list[SearchResult],
        symbols: list[str],
        evidence: list[str],
        *,
        failure: ReasonCode | None = None,
        from_cache: bool = False,
        foreign: str | None = None,
    ) -> AdvisoryResearch:
        links = list(osv) + self._tavily_links(results, {link.url for link in osv})
        known = {link.url for link in links}
        identified = bool(symbols)
        if identified:
            reason = None
        elif failure == "tavily_failed":
            reason = f"{NOT_IDENTIFIED_PREFIX}: the search failed"
        elif failure == "nebius_failed":
            reason = f"{NOT_IDENTIFIED_PREFIX}: the model call failed"
        elif foreign:
            reason = f"{NOT_IDENTIFIED_PREFIX}: {foreign}"
        else:
            reason = f"{NOT_IDENTIFIED_PREFIX} in the advisory text or search results"
        return AdvisoryResearch(
            advisory_id=advisory_id,
            identified=identified,
            not_identified_reason=reason,
            from_cache=from_cache,
            symbols=symbols,
            links=links,
            evidence_urls=[url for url in evidence if url in known],
            failure=failure,
        )

    # ---- cache (7 days, ADR-0031) -----------------------------------------------------

    def _cache_path(self, advisory_id: str) -> Path | None:
        if not is_valid_osv_id(advisory_id):
            return None
        return self._cache_dir / f"{advisory_id}.json"

    def _read_cache(
        self, advisory_id: str, osv: list[AdvisoryLink], package: str
    ) -> AdvisoryResearch | None:
        path = self._cache_path(advisory_id)
        if path is None:
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning(
                "research cache entry unreadable; refetching", extra={"service": "research"}
            )
            return None
        try:
            fetched_at = stored["fetched_at"]
            if not isinstance(fetched_at, int | float) or self._clock() - fetched_at > CACHE_TTL_S:
                return None
            results = [
                SearchResult(url=item["url"], title=item["title"], content="")
                for item in stored["tavily_links"]
            ]
            symbols = stored["symbols"]
            if not all(isinstance(s, str) and _SYMBOL_RE.fullmatch(s) for s in symbols):
                return None
            # An entry written before the package check may name another module's function.
            root = _import_root(package)
            kept = [s for s in symbols if _provided_by(s, root)]
            if symbols and not kept:
                return None
            return self._result(
                advisory_id,
                osv,
                results,
                kept,
                stored["evidence_urls"],
                from_cache=True,
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "research cache entry invalid; refetching", extra={"service": "research"}
            )
            return None

    def _write_cache(
        self, advisory_id: str, research: AdvisoryResearch, results: list[SearchResult]
    ) -> None:
        path = self._cache_path(advisory_id)
        if path is None or research.failure is not None:
            return
        returned = {result.url for result in results}
        entry = {
            "fetched_at": self._clock(),
            "symbols": research.symbols,
            "evidence_urls": research.evidence_urls,
            "tavily_links": [
                {"url": link.url, "title": link.title}
                for link in research.links
                if link.source == "tavily" and link.url in returned
            ],
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(entry), "utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.warning("research cache write failed", extra={"service": "research"})
            with contextlib.suppress(OSError):
                tmp.unlink()


class _StageFailed(Exception):
    """Internal: a stage failed with a reason code the caller reports."""

    def __init__(self, failure: ReasonCode) -> None:
        super().__init__(failure)
        self.failure = failure
