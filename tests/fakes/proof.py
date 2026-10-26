"""Run the real `proof-run` harness in a local subprocess against a fixture repository.

The fixture repositories under `tests/fixtures/repos/proof_*` hold a `repo/` folder (the repository)
and a `deps/` folder (a stub of the wheel the Sandbox would install). The driver runs
`proof.proof_run_command` exactly as the agent does, with the repository and dependency folders
pointed at the fixture, so the harness leaves with `os._exit(0)` after its one result line.
"""

import functools
import hashlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pir.executor.base import ExecResult
from pir.sandbox_agent.protocol import AgentError

SRC = Path(__file__).resolve().parents[2] / "src"
REPOS = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
PYPI_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "http" / "pypi"

DRIVER = """
import sys
sys.path.insert(0, sys.argv[1])
from pir.sandbox_agent import proof
repo, deps, entry, target, site, watchdog = sys.argv[2:8]
proof.WATCHDOG_S = float(watchdog)
args = {"entry": entry, "target": target}
if site:
    args["site"] = site
proof.proof_run_command(args, repo_root=repo, deps_root=deps)
print("UNREACHED")
"""


def fixture(name: str) -> tuple[Path, Path]:
    """`(repo, deps)` folders of a proof fixture."""
    return REPOS / name / "repo", REPOS / name / "deps"


def run_harness_subprocess(
    repo: Path, deps: Path, entry: str, target: str, site: str = "", watchdog: float = 30.0
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", DRIVER, str(SRC), str(repo), str(deps)]
        + [entry, target, site, str(watchdog)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return done, time.monotonic() - started


@functools.cache
def recorded_exec_result(
    name: str, entry: str, target: str, site: str = "", watchdog: float = 30.0
) -> ExecResult:
    """What the Sandbox returns for `proof-run` on fixture `name`: the harness's real output."""
    repo, deps = fixture(name)
    done, seconds = run_harness_subprocess(repo, deps, entry, target, site, watchdog)
    return ExecResult(
        exit_code=done.returncode,
        stdout=done.stdout,
        stderr=done.stderr,
        timed_out=False,
        truncated=False,
        duration_s=seconds,
        cost_usd=0.0,
        image=None,
    )


def line_of(path: Path, text: str) -> int:
    """The 1-based line number of the first line of `path` containing `text`."""
    for number, content in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in content:
            return number
    raise AssertionError(f"{text!r} not in {path}")


# ---- proof-install's source-package download (ADR-0041) ---------------------------------------

SDIST_BYTES = b"not a real archive: the agent must never open it"
SDIST_FILE = "PyYAML-5.3.1.tar.gz"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FakeResponse(io.BytesIO):
    """What an agent `opener` returns: a readable body with an HTTP status."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status


class FakePyPI:
    """An agent `opener` that serves `routes` (URL -> body bytes, an int HTTP status, an exception
    to raise or a callable returning a response). Any other URL is a 404, as `open_pypi` reports
    it. Records every URL asked for in `urls`."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = dict(routes)
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if url not in self.routes:
            raise AgentError("download failed: HTTP 404")
        item = self.routes[url]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        if isinstance(item, int):
            return FakeResponse(b"", item)
        return FakeResponse(item)


def json_url(name: str, version: str) -> str:
    return f"https://pypi.org/pypi/{name}/{version}/json"


def fake_release(
    routes: dict[str, Any],
    fixture_file: str,
    name: str,
    version: str,
    filename: str,
    content: bytes,
    *,
    sha: str | None = None,
    declared_size: int | None = None,
) -> dict[str, Any]:
    """Serve the recorded PyPI JSON page `fixture_file`, with the entry `filename` rewritten to
    describe `content` (its real sha256 and size unless overridden), and serve `content` at that
    entry's URL. Returns the entry."""
    data = json.loads((PYPI_FIXTURES / fixture_file).read_text(encoding="utf-8"))
    entry = next(u for u in data["urls"] if u["filename"] == filename)
    entry["digests"]["sha256"] = sha or sha256(content)
    entry["size"] = len(content) if declared_size is None else declared_size
    routes[json_url(name, version)] = json.dumps(data).encode("utf-8")
    routes[entry["url"]] = content
    return entry


def good_pypi(
    sdist: bytes = SDIST_BYTES,
) -> tuple[dict[str, Any], dict[str, tuple[str, str]]]:
    """Routes for pyyaml 5.3.1 plus the two build-tool wheels, and the `BUILD_BACKEND_WHEELS`
    constants that match the served wheels (monkeypatch them in)."""
    routes: dict[str, Any] = {}
    fake_release(routes, "pyyaml-5.3.1.json", "pyyaml", "5.3.1", SDIST_FILE, sdist)
    constants: dict[str, tuple[str, str]] = {}
    for name, version in (("setuptools", "75.8.0"), ("wheel", "0.45.1")):
        wheel = f"{name}-{version}-py3-none-any.whl"
        content = f"fake {wheel}".encode()
        fake_release(routes, f"{name}-{version}.json", name, version, wheel, content)
        constants[name] = (version, sha256(content))
    return routes, constants
