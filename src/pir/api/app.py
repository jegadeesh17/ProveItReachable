"""The app factory: `create_app` is the only way an app is built (ARCHITECTURE section 22)."""

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from pir.api.errors import ApiError, error_response, request_invalid
from pir.api.routes import ApiContext, create_router
from pir.api.security_headers import BodyLimitMiddleware, SecurityHeadersMiddleware
from pir.config import Settings, load_settings
from pir.deps import PipelineDeps, build_deps
from pir.jobs import JobManager
from pir.ratelimit import ClientHasher, RateLimiter, ReplayLimiter
from pir.replay import ReplayStore
from pir.spend import SystemClock

logger = logging.getLogger("pir.api")

# The Windows registry can map .js to text/plain; the CSP and module scripts need the real types.
for _suffix, _type in ((".js", "text/javascript"), (".css", "text/css"), (".woff2", "font/woff2")):
    mimetypes.add_type(_type, _suffix)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _project_path(relative: str) -> Path:
    """`relative` under the working directory (the Docker WORKDIR), else under the source tree."""
    for base in (Path.cwd(), _PROJECT_ROOT):
        candidate = base / relative
        if candidate.exists():
            return candidate
    return Path.cwd() / relative


def create_app(
    deps: PipelineDeps | None = None,
    settings: Settings | None = None,
    *,
    web_dir: Path | None = None,
    eval_path: Path | None = None,
    replay_store: ReplayStore | None = None,
) -> FastAPI:
    """Build the API and UI app.

    `deps` is what live scans use; with none given they are built from `settings` when
    LIVE_SCANS_ENABLED is true, and nothing is built when it is false (replays need no keys).
    `web_dir` and `eval_path` default to `web/` and `eval/results/latest.json`; the recordings
    are loaded from REPLAY_DIR unless a `replay_store` is given.
    """
    if settings is None:
        settings = deps.settings if deps is not None else load_settings()
    owns_deps = False
    if deps is None and settings.live_scans_enabled:
        deps = build_deps(settings, spender="hosted")
        owns_deps = True
    clock = deps.clock if deps is not None else SystemClock()
    ctx = ApiContext(
        settings=settings,
        deps=deps,
        jobs=JobManager(clock, ttl_s=settings.job_ttl_s, max_jobs=settings.max_jobs),
        limiter=RateLimiter.from_settings(settings),
        hasher=ClientHasher(),
        clock=clock,
        eval_path=eval_path or _project_path("eval/results/latest.json"),
        replays=replay_store or ReplayStore(_project_path(settings.replay_dir)),
        replay_limiter=ReplayLimiter.from_settings(settings),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if ctx.deps is not None:
            await ctx.deps.meter.refresh()
        try:
            yield
        finally:
            await ctx.jobs.shutdown()
            if owns_deps and ctx.deps is not None:
                await ctx.deps.aclose()

    app = FastAPI(
        title="ProveItReachable",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.ctx = ctx

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> Response:  # type: ignore[misc]
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, _exc: RequestValidationError) -> Response:  # type: ignore[misc]
        return error_response(request_invalid("a request parameter is not valid"))

    app.include_router(
        create_router(
            markdown_export=settings.feature_markdown_export,
            max_sse_streams=settings.max_sse_streams if settings.feature_progress_feed else None,
        )
    )

    static_dir = web_dir or _project_path("web")
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:
        logger.warning("web directory not found; the UI is not served")

    # Added last = outermost: the headers cover everything, the body cap sits just inside them.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
