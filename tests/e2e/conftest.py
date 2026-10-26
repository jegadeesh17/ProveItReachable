"""Browser end-to-end fixtures (M2-TASK-07, slow).

`create_app()` runs under uvicorn on a loopback port, in a thread of the test process, so the
tests can read the fakes behind it. Live scans run the real pipeline on small local repositories
with `FakeLLM`, recorded OSV and Tavily answers and `FakeExecutor`; the repository named in the
scanned URL (`github.com/octo/<name>`) picks the outcome. Replays are the committed recordings in
`demo/replays/`. No test reaches an external service: the autouse guard in `tests/conftest.py`
refuses any non-loopback connection.
"""

# ruff: noqa: E501 (the embedded browser scripts read better on one line)

import asyncio
import json
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fakes.deps import (
    APP,
    GHSA,
    NVD_URL,
    OTHER_GHSA,
    REPO_FILES,
    RESEARCH_REPLY,
    SHA,
    VERDICT,
    Harness,
    make_harness,
    osv_routes,
    write_repo,
)
from fakes.http import json_response
from fakes.llm import FakeLLM, json_result, tool_result
from playwright.sync_api import Browser, BrowserContext, Page

from pir.api import routes
from pir.api.app import create_app
from pir.github import (
    GitHubUnavailable,
    NotPythonProject,
    RepoCoordinates,
    RepoNotFound,
    RepoTooLarge,
    ValidatedTarget,
)
from pir.llm import LLMError
from pir.replay import ReplayStore

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
REPLAYS = ROOT / "demo" / "replays"
LOCKS = ROOT / "tests" / "fixtures" / "repos" / "locks" / "uv"

OWNER = "octo"
REPO_URL = "https://github.com/octo/{name}"
LATEST = {
    "scored": True,
    "run_date": "2026-10-25",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "experiment": "eval-x",
    "n": 14,
    "errored": 0,
    "precision": 1.0,
    "recall": 0.5,
    "abstention_rate": 0.2,
    "bar": 0.8,
    "bar_met": True,
}

# One local repository per outcome. The URL's repository name selects it.
REPOS: dict[str, dict[str, str]] = {
    # PyYAML 5.3.1 + yaml.full_load at app.py:5 -> one `likely reachable`.
    "demo": REPO_FILES,
    # A pinned package OSV knows nothing about -> clean.
    "clean": {"requirements.txt": "click==8.1.7\n", "app.py": "import click\n"},
    # A uv.lock with one source that cannot be resolved -> incomplete.
    "incomplete": {
        n: (LOCKS / n).read_text(encoding="utf-8") for n in ("pyproject.toml", "uv.lock")
    },
    # PyYAML 5.3 has two advisories; the model fails on the second -> likely reachable + uncertain.
    "partial": {"requirements.txt": "PyYAML==5.3\n", "app.py": APP},
    # OSV answers 500 for flask -> a not-checked, uncertain finding.
    "osvdown": {"requirements.txt": "flask==2.0.0\n", "app.py": "import flask\n"},
}
# Repositories that fail at intake, with the error each one raises.
INTAKE_ERRORS: dict[str, Callable[[], Exception]] = {
    "missing": RepoNotFound,
    "huge": lambda: RepoTooLarge(max_mb=50, max_files=5000, mb=120.5, files=9000),
    "notpython": lambda: NotPythonProject("no .py files"),
    "ghdown": GitHubUnavailable,
}
REPO_DIRS: dict[str, Path] = {}
_PATCH_LOCK = threading.Lock()


def _validated(coords: RepoCoordinates) -> ValidatedTarget:
    return ValidatedTarget(
        coords=coords,
        default_branch="main",
        commit_sha=SHA,
        files_total=3,
        bytes_total=1000,
        py_files=1,
        manifests=["requirements.txt"],
    )


async def _validate(coords: RepoCoordinates, ref: str | None) -> ValidatedTarget:
    if coords.name in INTAKE_ERRORS:
        raise INTAKE_ERRORS[coords.name]()
    return _validated(coords)


def _querybatch(request):
    queries = json.loads(request.content)["queries"]
    results = []
    for query in queries:
        name, version = query["package"]["name"], query.get("version")
        if name == "flask":
            return json_response(500, {})
        ids = (
            {"5.3.1": [GHSA], "5.3": [GHSA, OTHER_GHSA]}.get(version, [])
            if name == "pyyaml"
            else []
        )
        results.append({"vulns": [{"id": i} for i in ids]})
    return json_response(200, {"results": results})


def _llm_step(call):
    """Stateless, so concurrent findings and many scans can share one FakeLLM."""
    text = "\n".join(m.content or "" for m in call.messages)
    if call.json_mode:
        if OTHER_GHSA in text:
            raise LLMError("timeout", "model call timed out")
        return json_result({**RESEARCH_REPLY, "evidence_urls": [NVD_URL]})
    if any(m.role == "tool" for m in call.messages):
        return tool_result("submit_verdict", VERDICT)
    return tool_result("find_call_sites", {"symbol": "yaml.full_load"})


@dataclass
class LiveServer:
    """An app under uvicorn on 127.0.0.1, with the fakes behind it."""

    app: Any
    harness: Harness
    sock: socket.socket
    server: uvicorn.Server = field(init=False)
    thread: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        config = uvicorn.Config(
            self.app, log_level="warning", access_log=False, timeout_graceful_shutdown=2
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def start(self) -> "LiveServer":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the e2e server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()

    def api(self, path: str) -> httpx.Response:
        return httpx.get(self.url + path, timeout=10)


def start_server(
    tmp: Path,
    *,
    live: bool = True,
    paced: bool = False,
    eval_file: bool = True,
    **settings_kw: Any,
) -> LiveServer:
    values: dict[str, Any] = {
        "rate_limit_per_client_hour": 1000,
        "rate_limit_per_client_day": 1000,
        "rate_limit_global_day": 1000,
        "rate_limit_replays_per_client_hour": 1000,
        "max_concurrent_scans": 5,
        "live_scans_enabled": live,
    }
    values.update(settings_kw)
    h = make_harness(
        tmp / "h",
        llm=FakeLLM([_llm_step]),
        osv={**osv_routes(), "POST https://api.osv.dev/v1/querybatch": _querybatch},
        **values,
    )
    h.deps.validate = _validate

    async def quick(_seconds: float) -> None:
        await asyncio.sleep(0.03)

    store = ReplayStore(REPLAYS, **({} if paced else {"sleep": quick}))
    eval_path = tmp / "latest.json"
    if eval_file:
        eval_path.write_text(json.dumps(LATEST), encoding="utf-8")
    app = create_app(
        h.deps if live else None,
        h.settings,
        web_dir=WEB,
        eval_path=eval_path,
        replay_store=store,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return LiveServer(app, h, sock).start()


@pytest.fixture(scope="session", autouse=True)
def _fixture_repos(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Write the local repositories once and make live scans scan them instead of GitHub."""
    base = tmp_path_factory.mktemp("repos")
    for name, files in REPOS.items():
        REPO_DIRS[name] = write_repo(base / name, files)
    real_run_scan = routes.run_scan

    async def fixture_scan(deps, target, budget, **kwargs):
        repo = REPO_DIRS.get(target.coords.name, REPO_DIRS["demo"])
        return await real_run_scan(deps, Harness.target(repo), budget, **kwargs)

    with _PATCH_LOCK:
        routes.run_scan = fixture_scan
    yield
    with _PATCH_LOCK:
        routes.run_scan = real_run_scan


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """Live scans on, every feature on, quick replays, an eval figure published."""
    s = start_server(tmp_path_factory.mktemp("main"))
    yield s
    s.stop()


@pytest.fixture(scope="session")
def paced_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """Replays run at their real pace (about 20 s), as a judge would see them."""
    s = start_server(tmp_path_factory.mktemp("paced"), paced=True)
    yield s
    s.stop()


@pytest.fixture(scope="session")
def flags_off_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """progress_feed and markdown_export are off."""
    s = start_server(
        tmp_path_factory.mktemp("flags"),
        feature_progress_feed=False,
        feature_markdown_export=False,
    )
    yield s
    s.stop()


@pytest.fixture(scope="session")
def paused_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """LIVE_SCANS_ENABLED=false: replays only, no eval file."""
    s = start_server(tmp_path_factory.mktemp("paused"), live=False, eval_file=False)
    yield s
    s.stop()


@pytest.fixture
def limited_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """One live scan per client per hour; a fresh server per test so the quota is untouched."""
    s = start_server(tmp_path_factory.mktemp("limited"), rate_limit_per_client_hour=1)
    yield s
    s.stop()


# ---- browser ------------------------------------------------------------------------------

RECORDER = """
window.__stages = [];
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective));
new MutationObserver((records) => {
  for (const r of records) {
    if (r.type === 'attributes' && r.target.matches('.stage')) {
      window.__stages.push([r.target.dataset.step, r.target.dataset.state]);
    }
    for (const n of r.addedNodes) {
      if (n.nodeType === 1 && n.matches('.stage')) window.__stages.push([n.dataset.step, n.dataset.state]);
    }
  }
}).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-state'] });
"""

# The browser reports a refused request as a console error; tests that cause one say so.
RESOURCE_NOISE = "Failed to load resource"


class Ui:
    """One page in its own context, with everything the browser reports collected."""

    def __init__(self, context: BrowserContext, base: str) -> None:
        self.context = context
        self.base = base
        self.page: Page = context.new_page()
        self.console: list[tuple[str, str]] = []
        self.page_errors: list[str] = []
        self.requests: list[str] = []
        self.navigations = 0
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on(
            "framenavigated",
            lambda f: setattr(self, "navigations", self.navigations + (f == self.page.main_frame)),
        )

    def goto(self, query: str = "") -> "Ui":
        self.page.goto(f"{self.base}/{query}")
        self.page.locator("button[data-demo], .notice--error").first.wait_for()
        return self

    def problems(self, allow_resource_errors: bool = False) -> list[str]:
        """Console errors, uncaught exceptions and CSP violations seen so far."""
        found = [t for kind, t in self.console if kind == "error"]
        if allow_resource_errors:
            found = [t for t in found if RESOURCE_NOISE not in t]
        return found + self.page_errors + self.csp_violations()

    def csp_violations(self) -> list[str]:
        return self.page.evaluate("window.__csp || []")

    def stage_log(self) -> list[tuple[str, str]]:
        return [tuple(x) for x in self.page.evaluate("window.__stages")]

    def run_demo(self, slug: str) -> None:
        self.page.locator(f"button[data-demo={slug}]").click()

    def scan(self, repo_url: str) -> None:
        self.page.get_by_label("GitHub repository URL").fill(repo_url)
        self.page.get_by_role("button", name="Scan repo").click()

    def wait_report(self, timeout: float = 30_000) -> None:
        self.page.locator("#report .funnel").wait_for(timeout=timeout)
        self.page.locator("#report:not([aria-busy])").wait_for(state="attached")

    def patch_report(self, mutate: Callable[[dict], None]) -> None:
        """Let the real report through, but change it first (long text, hostile text, a proof)."""

        def handle(route) -> None:
            response = route.fetch()
            data = response.json()
            mutate(data)
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            route.fulfill(response=response, headers=headers, body=json.dumps(data))

        self.page.route(re.compile(r"/api/scans/[^/]+/report$"), handle)

    def fail(
        self, path: re.Pattern[str], status: int, code: str, message: str, **headers: str
    ) -> None:
        """Answer a request with an API error instead of letting it through."""
        body = json.dumps({"error": {"code": code, "message": message, "details": {}}})
        self.page.route(
            path,
            lambda route: route.fulfill(
                status=status,
                headers={"Content-Type": "application/json", **headers},
                body=body,
            ),
        )

    def scan_id(self) -> str:
        href = self.page.locator("#export-slot a").first.get_attribute("href")
        return href.split("/api/scans/")[1].split("/")[0]


@pytest.fixture
def ui_factory(browser: Browser) -> Iterator[Callable[..., Ui]]:
    contexts: list[BrowserContext] = []

    def make(
        base: str, *, width: int = 1280, height: int = 900, reduced_motion: bool = False
    ) -> Ui:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce" if reduced_motion else "no-preference",
            accept_downloads=True,
        )
        context.set_default_timeout(20_000)
        context.add_init_script(RECORDER)
        contexts.append(context)
        return Ui(context, base)

    yield make
    for context in contexts:
        context.close()
