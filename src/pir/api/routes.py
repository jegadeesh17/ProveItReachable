"""API routes of ARCHITECTURE section 8: scans, status, report, config, health, schema.

The events route lives in `pir.api.sse`; the proof route belongs to a later task. Both get the
same `ApiContext` through `request.app.state.ctx` (see `get_context`).
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.convertors import Convertor, register_url_convertor

from pir import __version__, messages
from pir.api.errors import (
    LIVE_SCANS_DISABLED_REASON,
    ApiError,
    admission_error,
    minutes_for,
    request_invalid,
)
from pir.api.sse import create_sse_router
from pir.config import Settings
from pir.deps import PipelineDeps
from pir.evaluation.metrics import load_eval_figure
from pir.github import InvalidRepoUrl, RepoCoordinates, parse_repo_url
from pir.jobs import JobManager, ScanNotFound
from pir.logs import bind_scan, scan_id_var
from pir.markdown import render_markdown
from pir.models.api import (
    AppConfigResponse,
    DemoSummary,
    HealthResponse,
    ScanAccepted,
    ScanRequest,
)
from pir.models.report import ScanReport
from pir.pipeline import github_repo_ref, run_scan
from pir.ratelimit import ClientHasher, Limited, RateLimiter, ReplayLimiter, client_key
from pir.replay import DemoNotFound, EventSink, ReplayStore
from pir.spend import Clock, Refused

logger = logging.getLogger("pir.api")

_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_SCAN_FIELDS = ("repo_url", "ref")
_DEMO_LIST = TypeAdapter(list[DemoSummary])


class _AnySlug(Convertor[str]):
    """Matches anything, slashes and newlines included, so that no demo path reaches the static
    mount and every non-allowlisted slug is a `demo_not_found`."""

    regex = r"(?s:.*)"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("anyslug", _AnySlug())


@dataclass
class ApiContext:
    """Everything a route needs. `deps` is None when live scans are switched off."""

    settings: Settings
    deps: PipelineDeps | None
    jobs: JobManager
    limiter: RateLimiter
    hasher: ClientHasher
    clock: Clock
    eval_path: Path
    replays: ReplayStore
    replay_limiter: ReplayLimiter
    live_in_flight: int = 0

    def acquire_live_slot(self) -> bool:
        """Take one of MAX_CONCURRENT_SCANS. No `await` between the check and the increment."""
        if self.live_in_flight >= self.settings.max_concurrent_scans:
            return False
        self.live_in_flight += 1
        return True

    def release_live_slot(self) -> None:
        self.live_in_flight = max(0, self.live_in_flight - 1)

    def client_key(self, request: Request) -> str:
        host = request.client.host if request.client else None
        forwarded = ", ".join(request.headers.getlist("x-forwarded-for")) or None
        return client_key(host, forwarded, self.settings.trusted_proxy_hops)

    def live_scans_paused(self) -> bool:
        return self.deps is None or self.deps.meter.status() == "paused"


def get_context(request: Request) -> ApiContext:
    return request.app.state.ctx


def json_response(model: BaseModel, status: int = 200, **kwargs: object) -> Response:
    return Response(
        content=model.model_dump_json(),
        status_code=status,
        media_type="application/json",
        **kwargs,  # type: ignore[arg-type]
    )


def parse_scan_request(body: bytes) -> tuple[ScanRequest, RepoCoordinates]:
    """Validate the request body. Raises `ApiError`; detail texts name a field, never echo input."""
    try:
        raw = json.loads(body)
    except ValueError:
        raise request_invalid("the body must be JSON") from None
    if not isinstance(raw, dict):
        raise request_invalid("the body must be a JSON object")
    url = raw.get("repo_url")
    if url is None:
        raise request_invalid("repo_url is required")
    if not isinstance(url, str):
        raise request_invalid("repo_url must be a string")
    if not url or len(url) > 300:
        raise ApiError("invalid_url")
    try:
        coords = parse_repo_url(url)
    except InvalidRepoUrl:
        raise ApiError("invalid_url") from None
    try:
        scan_request = ScanRequest.model_validate(raw)
    except ValidationError as exc:
        field = next(
            (str(e["loc"][0]) for e in exc.errors() if e["loc"] and e["loc"][0] in _SCAN_FIELDS),
            None,
        )
        raise request_invalid(
            f"{field} is not valid" if field else "the body has an unexpected field"
        ) from None
    return scan_request, coords


def create_router(*, markdown_export: bool = True, max_sse_streams: int | None = None) -> APIRouter:
    """`markdown_export=False` (FEATURE_MARKDOWN_EXPORT) leaves `report.md` unregistered (AC-64).
    `max_sse_streams` registers the events route with that stream cap; None (FEATURE_PROGRESS_FEED
    false) leaves it unregistered (AC-42)."""
    router = APIRouter(prefix="/api")
    if max_sse_streams is not None:
        router.include_router(create_sse_router(max_streams=max_sse_streams))

    @router.post("/scans", status_code=202)
    async def start_scan(request: Request) -> Response:
        ctx = get_context(request)
        scan_request, coords = parse_scan_request(await request.body())
        deps = ctx.deps
        if not ctx.settings.live_scans_enabled or deps is None:
            raise ApiError("live_scans_paused")
        key = ctx.client_key(request)
        client = ctx.hasher.hash(key)
        verdict = ctx.limiter.check_and_consume(key, ctx.clock.now())
        if isinstance(verdict, Limited):
            _log_refusal("rate_limited", client)
            raise ApiError(
                "rate_limited",
                messages.rate_limited(minutes_for(verdict.retry_after_s)),
                retry_after_s=verdict.retry_after_s,
            )
        scan_id = uuid.uuid4().hex
        token = bind_scan(scan_id)
        try:
            budget = await deps.meter.reserve(scan_id)
            if isinstance(budget, Refused):
                _log_refusal(f"spend_{budget.reason}", client)
                if budget.reason == "daily_limit":
                    raise ApiError(
                        "rate_limited",
                        messages.rate_limited(minutes_for(budget.retry_after_s)),
                        retry_after_s=budget.retry_after_s,
                    )
                raise ApiError("live_scans_paused")
            handed_off = False
            slot = False
            try:
                if not ctx.acquire_live_slot():
                    _log_refusal("server_busy", client)
                    raise ApiError("server_busy", retry_after_s=60)
                slot = True
                try:
                    target = await deps.validate(coords, scan_request.ref)
                except Exception as exc:
                    refusal = admission_error(exc)
                    if refusal is None:
                        raise
                    _log_refusal(refusal.code, client)
                    raise refusal from None
                repo = github_repo_ref(target)

                async def runner(sink: EventSink) -> ScanReport:
                    try:
                        return await run_scan(
                            deps, target, budget, repo=repo, scan_id=scan_id, sink=sink
                        )
                    finally:
                        try:
                            await deps.meter.settle(budget)
                        finally:
                            ctx.release_live_slot()

                ctx.jobs.create("live", runner, scan_id=scan_id)
                handed_off = True
            finally:
                if not handed_off:
                    if slot:
                        ctx.release_live_slot()
                    await deps.meter.settle(budget)
            logger.info("scan admitted", extra={"event": "scan_admitted", "client": client})
        finally:
            scan_id_var.reset(token)
        return json_response(_accepted(scan_id, "live"), 202)

    @router.get("/scans/{scan_id}")
    async def scan_status(scan_id: str, request: Request) -> Response:
        return json_response(_job(get_context(request), scan_id).status())

    @router.get("/scans/{scan_id}/report")
    async def scan_report(scan_id: str, request: Request, download: str | None = None) -> Response:
        job = _job(get_context(request), scan_id)
        report = job.report
        if report is None:
            raise ApiError("report_not_ready")
        headers = {}
        if download == "1":
            name = _FILENAME_CHARS.sub("-", report.repo.name)
            sha7 = _FILENAME_CHARS.sub("-", report.repo.commit_sha[:7])
            headers["Content-Disposition"] = (
                f'attachment; filename="proveitreachable-{name}-{sha7}.json"'
            )
        return Response(
            content=report.model_dump_json(indent=2),
            media_type="application/json",
            headers=headers,
        )

    if markdown_export:

        @router.get("/scans/{scan_id}/report.md")
        async def scan_report_markdown(
            scan_id: str, request: Request, download: str | None = None
        ) -> Response:
            job = _job(get_context(request), scan_id)
            report = job.report
            if report is None:
                raise ApiError("report_not_ready")
            headers = {}
            if download == "1":
                name = _FILENAME_CHARS.sub("-", report.repo.name)
                sha7 = _FILENAME_CHARS.sub("-", report.repo.commit_sha[:7])
                headers["Content-Disposition"] = (
                    f'attachment; filename="proveitreachable-{name}-{sha7}.md"'
                )
            return Response(
                content=render_markdown(report),
                media_type="text/markdown; charset=utf-8",
                headers=headers,
            )

    @router.get("/demos")
    async def list_demos(request: Request) -> Response:
        return Response(
            content=_DEMO_LIST.dump_json(get_context(request).replays.list()),
            media_type="application/json",
        )

    @router.post("/demos/{slug:anyslug}/replay", status_code=202)
    async def start_replay(slug: str, request: Request) -> Response:
        """Replay a recording. Counted only against the replay caps: never against the live-scan
        rate limits or the spend meter, and it needs no keys, so it works while live scans are
        paused."""
        ctx = get_context(request)
        try:
            rec = ctx.replays.get(slug)  # a dict lookup; the slug never reaches the filesystem
        except DemoNotFound:
            raise ApiError("demo_not_found") from None
        key = ctx.client_key(request)
        client = ctx.hasher.hash(key)
        verdict = ctx.replay_limiter.acquire(key, ctx.clock.now())
        if isinstance(verdict, Limited):
            logger.info(
                "replay refused",
                extra={"event": "replay_refused", "outcome": verdict.scope, "client": client},
            )
            raise ApiError(
                "too_many_requests",
                messages.too_many_requests(minutes_for(verdict.retry_after_s)),
                retry_after_s=verdict.retry_after_s,
            )

        async def runner(sink: EventSink) -> ScanReport:
            try:
                return await ctx.replays.play(
                    rec, sink, max_seconds=ctx.settings.replay_max_seconds
                )
            finally:
                ctx.replay_limiter.release(key)

        try:
            job = ctx.jobs.create("replay", runner)
        except BaseException:
            ctx.replay_limiter.release(key)
            raise
        logger.info("replay admitted", extra={"event": "replay_admitted", "client": client})
        return json_response(_accepted(job.scan_id, "replay"), 202)

    @router.get("/config")
    async def app_config(request: Request) -> Response:
        ctx = get_context(request)
        settings = ctx.settings
        reason: str | None = None
        if not settings.live_scans_enabled:
            reason = LIVE_SCANS_DISABLED_REASON
        elif ctx.live_scans_paused():
            reason = messages.message("live_scans_paused")
        return json_response(
            AppConfigResponse(
                version=__version__,
                live_scans_enabled=reason is None,
                live_scans_paused_reason=reason,
                features={
                    "progress_feed": settings.feature_progress_feed,
                    "markdown_export": settings.feature_markdown_export,
                    "proof": settings.feature_proof,
                },
                limits={
                    "max_repo_mb": settings.max_repo_mb,
                    "max_repo_files": settings.max_repo_files,
                    "live_scans_per_client_hour": settings.rate_limit_per_client_hour,
                    "live_scans_per_client_day": settings.rate_limit_per_client_day,
                },
                eval=load_eval_figure(ctx.eval_path),
            )
        )

    @router.get("/health")
    async def health(request: Request) -> Response:
        ctx = get_context(request)
        live = "disabled"
        fractions = None
        if ctx.settings.live_scans_enabled:
            live = "paused" if ctx.live_scans_paused() else "enabled"
        if ctx.deps is not None:
            fractions = ctx.deps.meter.remaining_fractions()
        return json_response(
            HealthResponse(
                status="ok",
                version=__version__,
                live_scans=live,  # type: ignore[arg-type]
                budget_remaining=fractions,
            )
        )

    @router.get("/schema/report")
    async def report_schema() -> Response:
        return Response(
            content=json.dumps(ScanReport.model_json_schema()), media_type="application/json"
        )

    return router


def _job(ctx: ApiContext, scan_id: str):
    try:
        return ctx.jobs.get(scan_id)
    except ScanNotFound:
        raise ApiError("scan_not_found") from None


def _accepted(scan_id: str, mode: str) -> ScanAccepted:
    base = f"/api/scans/{scan_id}"
    return ScanAccepted(
        scan_id=scan_id,
        mode=mode,  # type: ignore[arg-type]
        status_url=base,
        events_url=f"{base}/events",
        report_url=f"{base}/report",
    )


def _log_refusal(reason: str, client: str) -> None:
    logger.info(
        "scan refused", extra={"event": "scan_refused", "outcome": reason, "client": client}
    )
