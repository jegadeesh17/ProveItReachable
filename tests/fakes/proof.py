"""Run the real `proof-run` harness in a local subprocess against a fixture repository.

The fixture repositories under `tests/fixtures/repos/proof_*` hold a `repo/` folder (the repository)
and a `deps/` folder (a stub of the wheel the Sandbox would install). The driver runs
`proof.proof_run_command` exactly as the agent does, with the repository and dependency folders
pointed at the fixture, so the harness leaves with `os._exit(0)` after its one result line.
"""

import functools
import subprocess
import sys
import time
from pathlib import Path

from pir.executor.base import ExecResult

SRC = Path(__file__).resolve().parents[2] / "src"
REPOS = Path(__file__).resolve().parents[1] / "fixtures" / "repos"

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
