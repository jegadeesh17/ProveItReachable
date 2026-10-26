"""Call-chain proof commands (ARCHITECTURE section 16, ADR-0018, ADR-0039). Standard library only.

`proof-install` (network ON, image A -> image P): one `uv pip install` per pin, from wheels only,
into `/work/deps`. Nothing from the repository influences uv (section 7.2): fresh empty working
folder, fixed environment, the common flags, `-- <name==version>` built here from re-validated pins.

`proof-run` (network OFF, image P): the only command that runs repository code. It stubs the name
the repository calls with `unittest.mock.patch(target, side_effect=recorder)`, then either calls a
function entry with `unittest.mock.sentinel` placeholders, runs a main-block module as a script
(`runpy.run_path(file, run_name="__main__")`, `sys.argv == [file]`, empty stdin) or imports a
module. The first counted call is recorded and ends the run. The harness writes exactly one result
line and leaves with `os._exit(0)`; a watchdog does the same shortly before the platform timeout.
Nothing the repository prints is copied into a result (it is untrusted).
"""

import ast
import asyncio
import importlib
import inspect
import io
import os
import re
import runpy
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest import mock

from . import resolve
from .index import MAX_PY_FILE_BYTES, _is_main_guard, module_name_for
from .protocol import AgentError, BadInput, LimitExceeded, emit_result, log, require_str

REPO_ROOT = "/work/repo"
DEPS_ROOT = "/work/deps"
WATCHDOG_S = 55.0  # the proof-run timeout (60 s) minus 5 s
CAPTURE_BYTES = 64 * 1024  # per captured stream
INSTALL_BUDGET_S = 270.0  # inside the 300 s proof-install timeout
MAX_PINS = 300
MAX_CLASS_NAME_CHARS = 100

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
ENTRY_RE = re.compile(
    rf"(?P<module>{_IDENT}(?:\.{_IDENT})*):(?P<name><module>|{_IDENT}(?:\.{_IDENT})*)"
)
TARGET_RE = re.compile(rf"{_IDENT}(?:\.{_IDENT})*")
SITE_RE = re.compile(r"(?P<file>[A-Za-z0-9._/-]{1,1024}):(?P<line>[1-9][0-9]{0,8})")
MODULE_ENTRY = "<module>"


# ---- proof-install ---------------------------------------------------------------------------


def _validate_pins(raw: object) -> list[tuple[str, str]]:
    """`[{"name", "version"}, ...]` re-checked here: a normalized name and a PEP 440 version."""
    if not isinstance(raw, list) or len(raw) > MAX_PINS:
        raise BadInput(f"pins must be a list of at most {MAX_PINS} items")
    pins: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"name", "version"}:
            raise BadInput("each pin needs exactly name and version")
        name, version = item["name"], item["version"]
        if not isinstance(name, str) or resolve.normalize_name(name) != name:
            raise BadInput("a pin name is not a normalized package name")
        if not resolve.is_pep440(version):
            raise BadInput("a pin version is not a PEP 440 version")
        if (name, version) not in pins:
            pins.append((name, version))
    return pins


def build_install_argv(spec: str, deps_root: str = DEPS_ROOT) -> list[str]:
    """One pin per call: wheels only, no dependencies, no build, `--` before the requirement."""
    return [
        resolve.UV_BIN,
        "pip",
        "install",
        "--no-deps",
        "--only-binary",
        ":all:",
        "--target",
        deps_root,
        *resolve.UV_COMMON_FLAGS,
        "--",
        spec,
    ]


def proof_install_command(
    args: dict[str, Any],
    *,
    deps_root: str = DEPS_ROOT,
    tmp_root: str = resolve.TMP_ROOT,
    runner: resolve.Runner | None = None,
) -> dict[str, Any]:
    """`proof-install` handler. Args: `pins`. Result: `installed` and `no_wheel` (`name==version`).

    A pin whose only distribution is a source package is listed in `no_wheel` (nothing is built).
    Any other uv failure, or running out of time, fails the command.
    """
    pins = _validate_pins(args.get("pins"))
    run = runner or subprocess.run
    uvwd = os.path.join(tmp_root, resolve.UVWD_DIR)
    resolve._mkdir_fresh(uvwd)  # fresh and empty: no earlier file can influence uv
    installed: list[str] = []
    no_wheel: list[str] = []
    deadline = time.monotonic() + INSTALL_BUDGET_S
    for name, version in pins:
        spec = f"{name}=={version}"
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise AgentError("proof-install ran out of time")
        try:
            done = run(
                build_install_argv(spec, deps_root),
                cwd=uvwd,
                env=dict(resolve.UV_ENV),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(resolve.UV_TIMEOUT_S, remaining),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"uv timed out installing {spec}") from exc
        except OSError as exc:
            raise AgentError(f"cannot start uv ({type(exc).__name__})") from exc
        stderr = done.stderr or ""
        if done.returncode == 0:
            installed.append(spec)
        elif resolve.BUILD_NEEDED_RE.search(stderr):
            no_wheel.append(spec)
        else:
            raise AgentError(f"uv failed installing {spec}: {resolve._printable_tail(stderr, 300)}")
    log(f"proof-install: {len(installed)} installed, {len(no_wheel)} without a wheel")
    return {"installed": installed, "no_wheel": no_wheel}


# ---- proof-run: the harness ------------------------------------------------------------------


class _Stop(BaseException):
    """Private: raised by the recorder to end the run at the first counted call."""


class _CappedRaw(io.RawIOBase):
    """A write-only byte sink that keeps the first `limit` bytes (stdout and stderr capture)."""

    def __init__(self, limit: int = CAPTURE_BYTES) -> None:
        super().__init__()
        self._kept = bytearray()
        self._limit = limit

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        room = self._limit - len(self._kept)
        if room > 0:
            self._kept += bytes(data[:room])
        return len(data)

    def text(self) -> str:
        return bytes(self._kept).decode("utf-8", errors="replace")


def _capture() -> tuple[_CappedRaw, io.TextIOWrapper]:
    raw = _CappedRaw()
    return raw, io.TextIOWrapper(raw, encoding="utf-8", errors="replace", write_through=True)


class _Recorder:
    """The `side_effect` of the stub: records the nearest repository frame, then stops the run."""

    def __init__(self, repo_root: str, site: tuple[str, int] | None) -> None:
        self.repo_root = os.path.normpath(repo_root)
        self.site = site
        self.call: tuple[str, int] | None = None
        self._lock = threading.Lock()

    def _relative(self, filename: str) -> str | None:
        path = os.path.normpath(filename)
        prefix = self.repo_root + os.sep
        if not path.startswith(prefix):
            return None
        return path[len(prefix) :].replace(os.sep, "/")

    def __call__(self, *args: object, **kwargs: object) -> object:
        frame = sys._getframe(1)
        while frame is not None:
            relative = self._relative(frame.f_code.co_filename)
            if relative is not None:
                break
            frame = frame.f_back
        else:
            return mock.DEFAULT  # called from library code only: not the repository's call
        where = (relative, frame.f_lineno)
        if self.site is not None and where != self.site:
            return mock.DEFAULT  # another call (for example one that passes a Loader)
        with self._lock:
            if self.call is None:
                self.call = where
        raise _Stop

    def recorded(self, target: str) -> str | None:
        call = self.call
        return None if call is None else f"{target} called from {call[0]}:{call[1]}"


@dataclass
class _Ended:
    """How the entry stopped when the recorder had not fired."""

    kind: str  # returned | exit | raised | no_entry | stub_failed | harness
    detail: str = ""


def _class_name(exc: BaseException) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "?", type(exc).__name__)[:MAX_CLASS_NAME_CHARS]


def _exit_code_text(code: object) -> str:
    if code is None:
        return "0"
    if isinstance(code, int):
        return str(int(code))
    return "1"  # a message string: Python exits with 1


def build_call(func: Callable[..., Any]) -> tuple[list[object], dict[str, object]]:
    """The arguments the harness passes to a function entry: `unittest.mock.sentinel`
    placeholders for every parameter without a default, nothing else (AC-69)."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return [], {}
    args: list[object] = []
    kwargs: dict[str, object] = {}
    for param in signature.parameters.values():
        if param.default is not param.empty or param.kind in (
            param.VAR_POSITIONAL,
            param.VAR_KEYWORD,
        ):
            continue
        placeholder = getattr(mock.sentinel, param.name)
        if param.kind is param.KEYWORD_ONLY:
            kwargs[param.name] = placeholder
        else:
            args.append(placeholder)
    return args, kwargs


def find_entry_file(repo_root: str, module: str) -> str | None:
    """Repo-relative path of the file whose index module name is `module` (`src/` is stripped)."""
    base = module.replace(".", "/")
    root = os.path.realpath(repo_root)
    for prefix in ("", "src/"):
        for candidate in (f"{prefix}{base}.py", f"{prefix}{base}/__init__.py"):
            path = os.path.join(repo_root, *candidate.split("/"))
            if (
                os.path.isfile(path)
                and os.path.realpath(path).startswith(root + os.sep)
                and module_name_for(candidate)[0] == module
            ):
                return candidate
    return None


def has_main_guard(path: str) -> bool:
    """The index's own guard check on the file's syntax tree (`ast` only; nothing runs)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_PY_FILE_BYTES + 1)
        if len(data) > MAX_PY_FILE_BYTES:
            return False
        tree = ast.parse(data, filename=path)
    except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return any(_is_main_guard(stmt) for stmt in tree.body)


def _run_function(module: str, name: str) -> None:
    target: Any = importlib.import_module(module)
    for part in name.split("."):
        target = getattr(target, part)
    args, kwargs = build_call(target)
    result = target(*args, **kwargs)
    if inspect.isawaitable(result):

        async def await_it() -> None:
            await result

        asyncio.run(await_it())


def _attempt(
    entry: str,
    target: str,
    recorder: _Recorder,
    repo_root: str,
    deps_root: str,
) -> _Ended:
    """Stub the target, then run the entry. Returns how it ended (the recorder is read after)."""
    match = ENTRY_RE.fullmatch(entry)
    assert match is not None  # validated by the caller
    module, name = match.group("module"), match.group("name")
    relative = find_entry_file(repo_root, module)
    if relative is None:
        return _Ended("no_entry")
    path = os.path.join(repo_root, *relative.split("/"))
    main_block = name == MODULE_ENTRY and has_main_guard(path)
    roots = [repo_root, deps_root]
    if relative.startswith("src/"):
        roots.insert(0, os.path.join(repo_root, "src"))
    if main_block:
        roots.insert(0, os.path.dirname(path))  # as `python <file>` puts the file's folder first
    sys.path[:0] = roots
    sys.argv = [path]
    sys.stdin = io.StringIO("")
    patcher = mock.patch(target, side_effect=recorder)
    try:
        patcher.start()
    except Exception as exc:  # the target is not importable or has no such attribute
        return _Ended("stub_failed", _class_name(exc))
    try:
        if name != MODULE_ENTRY:
            _run_function(module, name)
        elif main_block:
            os.chdir(repo_root)
            runpy.run_path(path, run_name="__main__")
        else:
            importlib.import_module(module)
        return _Ended("returned")
    except _Stop:
        return _Ended("returned")  # the recorder holds the call; the caller reads it first
    except SystemExit as exc:
        return _Ended("exit", _exit_code_text(exc.code))
    except BaseException as exc:  # noqa: BLE001 - any ending of repository code is a stated result
        return _Ended("raised", _class_name(exc))
    finally:
        try:
            patcher.stop()
        except RuntimeError:
            log("the stub was already removed")


def _reason(ended: _Ended, target: str, stderr: str) -> str:
    if ended.kind == "exit":
        if ended.detail == "2" and stderr.lstrip().startswith("usage:"):
            return (
                "entry needs command-line arguments; the proof supplies none "
                f"(exit code 2 before reaching {target})"
            )
        return f"entry exited with code {ended.detail} before reaching {target}"
    if ended.kind == "raised":
        return f"entry raised {ended.detail} before reaching {target}"
    if ended.kind == "no_entry":
        return "entry module was not found in the repository"
    if ended.kind == "stub_failed":
        return f"could not stub {target}: {ended.detail}"
    if ended.kind == "harness":
        return f"the proof harness failed: {ended.detail}"
    return f"entry finished without reaching {target}"


class _Reporter:
    """Writes exactly one result line, then calls `finish(0)` (`os._exit` in the Sandbox)."""

    def __init__(self, stdout: Any, finish: Callable[[int], object]) -> None:
        self._stdout = stdout
        self._finish = finish
        self._lock = threading.Lock()
        self._done = False

    def report(self, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            code = 0
            try:
                emit_result(payload, self._stdout)
            except LimitExceeded:
                code = 3
            except (OSError, ValueError) as exc:  # the result line could not be written
                log(f"cannot write the proof result: {type(exc).__name__}")
                code = 1
            try:
                sys.__stderr__.flush()  # type: ignore[union-attr]
            except (OSError, ValueError, AttributeError):
                pass  # stderr is only diagnostics; the result line is already written
            self._finish(code)


def _parse_args(args: dict[str, Any]) -> tuple[str, str, tuple[str, int] | None]:
    entry = require_str(args, "entry")
    target = require_str(args, "target")
    if len(entry) > 300 or ENTRY_RE.fullmatch(entry) is None:
        raise BadInput("entry must look like pkg.mod:func or pkg.mod:<module>")
    if len(target) > 300 or TARGET_RE.fullmatch(target) is None:
        raise BadInput("target must be a dotted name")
    site: tuple[str, int] | None = None
    raw = args.get("site")
    if raw is not None:
        match = SITE_RE.fullmatch(raw) if isinstance(raw, str) else None
        if match is None or ".." in match.group("file").split("/"):
            raise BadInput("site must look like path/to/file.py:12")
        if match.group("file").startswith("/"):
            raise BadInput("site must be a repo-relative path")
        site = (match.group("file"), int(match.group("line")))
    return entry, target, site


def proof_run_command(
    args: dict[str, Any],
    *,
    repo_root: str = REPO_ROOT,
    deps_root: str = DEPS_ROOT,
    finish: Callable[[int], object] = os._exit,
) -> dict[str, Any]:
    """`proof-run` handler. Args: `entry`, `target` and optionally `site` (`file:line`).

    Runs the entry with the target stubbed, reports once and leaves the process with
    `finish(0)`. It returns the payload only when `finish` returns (tests).
    """
    entry, target, site = _parse_args(args)
    reporter = _Reporter(sys.stdout, finish)
    recorder = _Recorder(repo_root, site)
    out_raw, out_stream = _capture()
    err_raw, err_stream = _capture()

    def payload(ended: _Ended | None) -> dict[str, Any]:
        recorded = recorder.recorded(target)
        if recorded is not None:  # reached: proven, even if repository code swallowed the stop
            return {
                "status": "proven",
                "reason": f"{target} was reached from {entry}",
                "entry": entry,
                "target": target,
                "recorded_call": recorded,
            }
        reason = "sandbox timeout" if ended is None else _reason(ended, target, err_raw.text())
        return {
            "status": "not_proven",
            "reason": reason,
            "entry": entry,
            "target": target,
            "recorded_call": None,
        }

    watchdog = threading.Timer(WATCHDOG_S, lambda: reporter.report(payload(None)))
    watchdog.daemon = True
    watchdog.start()
    saved = (sys.stdout, sys.stderr, sys.stdin, sys.argv)
    sys.stdout, sys.stderr = out_stream, err_stream
    try:
        ended = _attempt(entry, target, recorder, repo_root, deps_root)
    except BaseException as exc:  # noqa: BLE001 - a harness bug is still one stated result
        ended = _Ended("harness", _class_name(exc))
    finally:
        sys.stdout, sys.stderr, sys.stdin, sys.argv = saved
    result = payload(ended)
    reporter.report(result)
    watchdog.cancel()
    return result
