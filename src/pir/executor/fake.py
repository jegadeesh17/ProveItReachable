"""FakeExecutor (ARCHITECTURE section 7.1): tests and `--executor fake` with a local fixture.

Images are temp-directory copies. `run` handles only `unpack`, `resolve --lockfile-only` and
`index`, in-process through the agent's pure functions; anything else returns a recorded
`ExecResult` or exit code 99. It never starts a subprocess, costs nothing and refuses GitHub
targets (`fetch` is not handled).
"""

import io
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial

from pir.executor.base import ExecResult, ImageRef
from pir.sandbox_agent import fetch, index, protocol, resolve
from pir.spend import ScanBudget

logger = logging.getLogger("pir.executor.fake")

EXIT_UNSUPPORTED = 99
AGENT_PATH = "/opt/pir/agent.pyz"
REPO_SUBDIR = os.path.join("work", "repo")
HANDLED = ("unpack", "resolve --lockfile-only", "index")


@dataclass(frozen=True)
class RecordedCall:
    """One `run` as the fake saw it (AC-30: the index step goes through `Executor.run`)."""

    argv: tuple[str, ...]
    command: str
    network: bool
    timeout_s: float
    save: bool
    image: ImageRef


def _command_key(argv: list[str]) -> tuple[str, str, str] | None:
    """`(key, command, args_b64)` for an agent call `... agent.pyz <command> --args-b64 <b64>`."""
    if AGENT_PATH not in argv:
        return None
    tail = argv[argv.index(AGENT_PATH) + 1 :]
    if len(tail) != 3 or tail[1] != "--args-b64":
        return None
    try:
        args = protocol.parse_args_b64(tail[2])
    except protocol.BadInput:
        return None
    command = tail[0]
    key = f"resolve --{args.get('mode')}" if command == "resolve" else command
    return key, command, tail[2]


class FakeExecutor:
    kind = "fake"

    def __init__(self, recorded: Mapping[str, ExecResult] | None = None) -> None:
        """`recorded` maps a command key (for example "resolve --compile") to the result to return
        for commands the fake does not run itself."""
        self.recorded = dict(recorded or {})
        self.calls: list[RecordedCall] = []
        self._dirs: set[str] = set()

    def _new_dir(self) -> str:
        path = tempfile.mkdtemp(prefix="pir-fake-")
        self._dirs.add(path)
        return path

    async def prepare(self, files: dict[str, bytes]) -> ImageRef:
        root = self._new_dir()
        for path, content in files.items():
            if not path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"image path must be absolute without '..': {path!r}")
            target = os.path.join(root, *path.strip("/").split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
        os.makedirs(os.path.join(root, "work"), exist_ok=True)
        return ImageRef(kind="fake", ref=root)

    def _image_dir(self, image: ImageRef) -> str:
        if image.kind != "fake" or image.ref not in self._dirs or not os.path.isdir(image.ref):
            raise ValueError("FakeExecutor runs its own fake images only")
        return image.ref

    async def run(
        self,
        image: ImageRef,
        argv: list[str],
        *,
        network: bool,
        timeout_s: float,
        save: bool = False,
        budget: ScanBudget | None = None,
    ) -> ExecResult:
        source = self._image_dir(image)
        self.calls.append(
            RecordedCall(tuple(argv), " ".join(argv), network, timeout_s, save, image)
        )
        started = time.monotonic()
        parsed = _command_key(argv)
        key = parsed[0] if parsed else None
        if parsed is None or key not in HANDLED:
            return self._unsupported(key, started)
        _, command, args_b64 = parsed
        # Always work on a copy: a saved run keeps it (image A), any other run drops it.
        work = self._new_dir()
        shutil.copytree(source, work, dirs_exist_ok=True)
        repo = os.path.join(work, REPO_SUBDIR)
        handlers: dict[str, protocol.Handler] = {
            "unpack": partial(
                fetch.unpack,
                repo_root=repo,
                tarball=os.path.join(source, "opt", "pir", "src.tar.gz"),
            ),
            "index": partial(index.index_command, repo_root=repo),
            "resolve": partial(resolve.resolve_command, repo_root=repo),
        }
        out, err = io.StringIO(), io.StringIO()
        code = protocol.run_command([command, "--args-b64", args_b64], handlers, out, err)
        saved = ImageRef(kind="fake", ref=work) if save and code == 0 else None
        if saved is None:
            self._drop(work)
        return ExecResult(
            exit_code=code,
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            timed_out=False,
            truncated=False,
            duration_s=time.monotonic() - started,
            cost_usd=0.0,
            image=saved,
        )

    def _unsupported(self, key: str | None, started: float) -> ExecResult:
        recorded = self.recorded.get(key or "")
        if recorded is not None:
            return recorded
        return ExecResult(
            exit_code=EXIT_UNSUPPORTED,
            stdout="",
            stderr=f"FakeExecutor does not run {key or 'this command'!r}",
            timed_out=False,
            truncated=False,
            duration_s=time.monotonic() - started,
            cost_usd=0.0,
            image=None,
        )

    def _drop(self, path: str) -> None:
        self._dirs.discard(path)
        shutil.rmtree(path, ignore_errors=True)

    async def discard(self, image: ImageRef) -> None:
        """Remove the image's temp directory. Never raises."""
        try:
            if image.kind == "fake" and image.ref in self._dirs:
                self._drop(image.ref)
        except Exception as exc:  # discard never raises; the cause is logged
            logger.warning("removing a fake image failed: %s", type(exc).__name__)
