"""Call-chain proof commands (ARCHITECTURE section 16, ADR-0018, ADR-0039). Standard library only.

`proof-install` (network ON, image A -> image P): one `uv pip install` per pin, from wheels only,
into `/work/deps`. Nothing from the repository influences uv (section 7.2): fresh empty working
folder, fixed environment, the common flags, `-- <name==version>` built here from re-validated pins.
A pin without a wheel is only downloaded (ADR-0041): this module's own HTTPS code saves its source
package, checksum-checked and never opened, plus two checksum-pinned build-tool wheels.

`proof-build` (network OFF, image P -> image P'): builds each saved source package with uv, from the
two local build-tool wheels only (ADR-0041). Build output is never read.

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
import hashlib
import http.client
import importlib
import inspect
import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest import mock
from urllib.parse import quote

from . import fetch, resolve
from .index import MAX_PY_FILE_BYTES, _is_main_guard, module_name_for
from .protocol import AgentError, BadInput, LimitExceeded, emit_result, log, require_str

REPO_ROOT = "/work/repo"
DEPS_ROOT = "/work/deps"
WATCHDOG_S = 55.0  # the proof-run timeout (60 s) minus 5 s
CAPTURE_BYTES = 64 * 1024  # per captured stream
INSTALL_BUDGET_S = 270.0  # inside the 300 s proof-install timeout
MAX_PINS = 300

# ADR-0041: source packages (downloaded by proof-install, built by proof-build).
SDISTS_ROOT = "/work/sdists"
BUILDWHEELS_ROOT = "/work/buildwheels"
PIRBUILD_DIR = "pirbuild"
CONSTRAINTS_NAME = "build-constraints.txt"
UVBUILD_PREFIX = "uvbuild-"
MAX_SDISTS = 10
MAX_PYPI_JSON_BYTES = 5 * 1024 * 1024
MAX_SDIST_BYTES = 20 * 1024 * 1024
MAX_SDIST_TOTAL_BYTES = 50 * 1024 * 1024
BUILD_BUDGET_S = 270.0  # inside the 300 s proof-build timeout
BUILD_TIMEOUT_S = 120.0  # per package
# The two build tools every source build uses, as wheels. name -> (version, sha256). The sha256
# values are copied from PyPI's JSON API, read 2026-10-26:
#   https://pypi.org/pypi/setuptools/75.8.0/json (setuptools-75.8.0-py3-none-any.whl)
#   https://pypi.org/pypi/wheel/0.45.1/json (wheel-0.45.1-py3-none-any.whl)
BUILD_BACKEND_WHEELS: dict[str, tuple[str, str]] = {
    "setuptools": ("75.8.0", "e3982f444617239225d675215d51f6ba05f845d4eec313da4418fdbb56fb27e3"),
    "wheel": ("0.45.1", "708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248"),
}

# Reasons a source package was not saved (`unfetched[].reason`).
NO_SDIST = "no_sdist"
TOO_LARGE = "too_large"
HASH_MISMATCH = "hash_mismatch"
PYPI_ERROR = "pypi_error"
LIMIT = "limit"
TIME_LIMIT = "time_limit"
BACKEND_UNAVAILABLE = "backend_unavailable"
BUILD_FAILED = "build_failed"
BUILD_TIMEOUT = "build_timeout"
MAX_CLASS_NAME_CHARS = 100

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
ENTRY_RE = re.compile(
    rf"(?P<module>{_IDENT}(?:\.{_IDENT})*):(?P<name><module>|{_IDENT}(?:\.{_IDENT})*)"
)
TARGET_RE = re.compile(rf"{_IDENT}(?:\.{_IDENT})*")
SITE_RE = re.compile(r"(?P<file>[A-Za-z0-9._/-]{1,1024}):(?P<line>[1-9][0-9]{0,8})")
MODULE_ENTRY = "<module>"
SDIST_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.(?:tar\.gz|zip)")
FILES_URL_RE = re.compile(r"https://files\.pythonhosted\.org/packages/[A-Za-z0-9._~%+/-]{1,500}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


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


class _Unfetched(Exception):
    """Internal: a source package or build tool that could not be saved, with the stated reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"cannot remove {os.path.basename(path)}: {type(exc).__name__}")


def _open_ok(opener: fetch.Opener, url: str) -> Any:
    """The response for `url`; any failure (HTTP error, redirect, timeout, status other than 200)
    is `pypi_error`."""
    try:
        response = opener(url)
    except (AgentError, OSError, http.client.HTTPException) as exc:
        raise _Unfetched(PYPI_ERROR) from exc
    if getattr(response, "status", 200) != 200:
        response.close()
        raise _Unfetched(PYPI_ERROR)
    return response


def _pypi_json(opener: fetch.Opener, name: str, version: str) -> dict[str, Any]:
    """`https://pypi.org/pypi/<name>/<version>/json`, parsed (name and version are validated)."""
    url = f"{fetch.PYPI_JSON_PREFIX}{quote(name, safe='')}/{quote(version, safe='')}/json"
    response = _open_ok(opener, url)
    try:
        body = bytearray()
        while True:
            chunk = response.read(fetch.CHUNK)
            if not chunk:
                break
            body += chunk
            if len(body) > MAX_PYPI_JSON_BYTES:
                raise _Unfetched(PYPI_ERROR)
    except (AgentError, OSError, http.client.HTTPException) as exc:
        raise _Unfetched(PYPI_ERROR) from exc
    finally:
        response.close()
    try:
        data = json.loads(bytes(body).decode("utf-8"))
    except ValueError as exc:  # includes UnicodeDecodeError and JSONDecodeError
        raise _Unfetched(PYPI_ERROR) from exc
    if not isinstance(data, dict) or not isinstance(data.get("urls"), list):
        raise _Unfetched(PYPI_ERROR)
    return data


@dataclass
class _Candidate:
    filename: str
    packagetype: object
    url: str
    sha256: str
    size: int


def _candidates(data: dict[str, Any]) -> list[_Candidate]:
    """The `urls[]` entries whose URL, sha256 and size pass the rules (section 16)."""
    found: list[_Candidate] = []
    for item in data["urls"]:
        if not isinstance(item, dict):
            continue
        filename, url = item.get("filename"), item.get("url")
        digests, size = item.get("digests"), item.get("size")
        sha256 = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(filename, str) or not isinstance(url, str):
            continue
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            continue
        if not FILES_URL_RE.fullmatch(url) or ".." in url.split("/"):
            continue  # a foreign host, userinfo, port, query, fragment or odd characters
        found.append(_Candidate(filename, item.get("packagetype"), url, sha256, size))
    return found


def _download(
    opener: fetch.Opener, candidate: _Candidate, path: str, *, total_room: int, deadline: float
) -> int:
    """Stream `candidate` to `path` (exclusive create, never opened afterwards), hash it while it
    is written, and return its size. Any failure deletes the partial file."""
    response = _open_ok(opener, candidate.url)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        response.close()
        raise AgentError(f"cannot create {os.path.basename(path)} ({type(exc).__name__})") from exc
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as out:
            while True:
                if time.monotonic() > deadline:
                    raise _Unfetched(TIME_LIMIT)
                chunk = response.read(fetch.CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_SDIST_BYTES:
                    raise _Unfetched(TOO_LARGE)
                if written > total_room:
                    raise _Unfetched(LIMIT)
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != candidate.sha256:
            raise _Unfetched(HASH_MISMATCH)
    except _Unfetched:
        _remove(path)
        raise
    except (AgentError, OSError, http.client.HTTPException) as exc:
        _remove(path)
        raise _Unfetched(PYPI_ERROR) from exc
    finally:
        response.close()
    return written


def _is_sdist(candidate: _Candidate) -> bool:
    return (
        candidate.packagetype == "sdist" and SDIST_NAME_RE.fullmatch(candidate.filename) is not None
    )


def _fetch_sdist(
    opener: fetch.Opener,
    name: str,
    version: str,
    sdists_root: str,
    *,
    total_so_far: int,
    deadline: float,
) -> tuple[dict[str, str], int]:
    """Save one source package. Returns its `sdists` entry and its size."""
    candidates = [c for c in _candidates(_pypi_json(opener, name, version)) if _is_sdist(c)]
    if not candidates:
        raise _Unfetched(NO_SDIST)
    chosen = next((c for c in candidates if c.filename.endswith(".tar.gz")), candidates[0])
    if chosen.size > MAX_SDIST_BYTES:
        raise _Unfetched(TOO_LARGE)
    if total_so_far + chosen.size > MAX_SDIST_TOTAL_BYTES:
        raise _Unfetched(LIMIT)
    path = os.path.join(sdists_root, chosen.filename)
    written = _download(
        opener,
        chosen,
        path,
        total_room=MAX_SDIST_TOTAL_BYTES - total_so_far,
        deadline=deadline,
    )
    return {"spec": f"{name}=={version}", "file": path, "sha256": chosen.sha256}, written


def _fetch_backend_wheels(opener: fetch.Opener, wheels_root: str, deadline: float) -> None:
    """Save the two build-tool wheels. Each must equal PyPI's sha256 and the committed constant."""
    resolve._mkdir_fresh(wheels_root)
    for name, (version, expected) in BUILD_BACKEND_WHEELS.items():
        filename = f"{name}-{version}-py3-none-any.whl"
        data = _pypi_json(opener, name, version)
        chosen = next((c for c in _candidates(data) if c.filename == filename), None)
        if chosen is None or chosen.sha256 != expected or chosen.size > MAX_SDIST_BYTES:
            raise _Unfetched(BACKEND_UNAVAILABLE)
        _download(
            opener,
            chosen,
            os.path.join(wheels_root, filename),
            total_room=MAX_SDIST_BYTES,
            deadline=deadline,
        )


def _download_sources(
    no_wheel: list[str],
    opener: fetch.Opener,
    sdists_root: str,
    wheels_root: str,
    deadline: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Phase 1 (network ON): save each source package and, if any was saved, the build tools.
    Nothing is opened, unpacked or run. Returns `(sdists, unfetched)`, each in `no_wheel` order."""
    resolve._mkdir_fresh(sdists_root)
    saved: dict[str, dict[str, str]] = {}
    failed: dict[str, str] = {}
    total = 0
    for number, spec in enumerate(no_wheel):
        name, version = spec.split("==", 1)
        if number >= MAX_SDISTS:
            failed[spec] = LIMIT
        elif deadline - time.monotonic() < 1:
            failed[spec] = TIME_LIMIT
        else:
            try:
                entry, size = _fetch_sdist(
                    opener, name, version, sdists_root, total_so_far=total, deadline=deadline
                )
            except _Unfetched as exc:
                failed[spec] = exc.reason
            else:
                saved[spec] = entry
                total += size
    if saved:
        try:
            _fetch_backend_wheels(opener, wheels_root, deadline)
        except _Unfetched as exc:
            log(f"proof-install: build tools unavailable ({exc.reason})")
            for spec, entry in saved.items():
                _remove(entry["file"])
                failed[spec] = BACKEND_UNAVAILABLE
            saved.clear()
    sdists = [saved[spec] for spec in no_wheel if spec in saved]
    unfetched = [{"spec": spec, "reason": failed[spec]} for spec in no_wheel if spec in failed]
    return sdists, unfetched


def proof_install_command(
    args: dict[str, Any],
    *,
    deps_root: str = DEPS_ROOT,
    tmp_root: str = resolve.TMP_ROOT,
    runner: resolve.Runner | None = None,
    opener: fetch.Opener | None = None,
    sdists_root: str = SDISTS_ROOT,
    wheels_root: str = BUILDWHEELS_ROOT,
) -> dict[str, Any]:
    """`proof-install` handler. Args: `pins`.

    Result: `installed` and `no_wheel` (`name==version`), plus, for each `no_wheel` pin, either an
    `sdists` entry `{spec, file, sha256}` (its source package saved, never opened; ADR-0041) or an
    `unfetched` entry `{spec, reason}`. Any other uv failure, or running out of time, fails the
    command.
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
    sdists: list[dict[str, str]] = []
    unfetched: list[dict[str, str]] = []
    if no_wheel:
        sdists, unfetched = _download_sources(
            no_wheel, opener or fetch.open_pypi, sdists_root, wheels_root, deadline
        )
    log(
        f"proof-install: {len(installed)} installed, {len(no_wheel)} without a wheel "
        f"({len(sdists)} source packages saved)"
    )
    return {"installed": installed, "no_wheel": no_wheel, "sdists": sdists, "unfetched": unfetched}


# ---- proof-build -----------------------------------------------------------------------------


def build_build_argv(
    sdist_file: str,
    constraints_file: str,
    deps_root: str = DEPS_ROOT,
    wheels_root: str = BUILDWHEELS_ROOT,
) -> list[str]:
    """Build one saved source package offline; isolation is fed only by the local build wheels."""
    return [
        resolve.UV_BIN,
        "pip",
        "install",
        "--no-deps",
        "--target",
        deps_root,
        *resolve.UV_BUILD_COMMON_FLAGS,
        "--offline",
        "--no-index",
        "--find-links",
        wheels_root,
        "--build-constraints",
        constraints_file,
        "--",
        sdist_file,
    ]


def _sha256_of(path: str, label: str) -> str:
    """Hash a regular file the agent saved: lstat first (no links), then open without following."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BadInput(f"{label} is missing") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BadInput(f"{label} is not a regular file")
    if info.st_size > MAX_SDIST_BYTES:
        raise BadInput(f"{label} is over {MAX_SDIST_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    digest = hashlib.sha256()
    try:
        with os.fdopen(os.open(path, flags), "rb") as handle:
            for chunk in iter(lambda: handle.read(fetch.CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BadInput(f"{label} cannot be read") from exc
    return digest.hexdigest()


def _validate_builds(raw: object, sdists_root: str) -> list[tuple[str, str, str]]:
    """`[{"name", "version", "file", "sha256"}, ...]` re-checked: pins, the file under
    `sdists_root`, a regular file within the size cap whose recomputed sha256 matches."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SDISTS:
        raise BadInput(f"builds must be a list of 1 to {MAX_SDISTS} items")
    builds: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"name", "version", "file", "sha256"}:
            raise BadInput("each build needs exactly name, version, file and sha256")
        name, version = item["name"], item["version"]
        file, sha256 = item["file"], item["sha256"]
        if not isinstance(name, str) or resolve.normalize_name(name) != name:
            raise BadInput("a build name is not a normalized package name")
        if not resolve.is_pep440(version):
            raise BadInput("a build version is not a PEP 440 version")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise BadInput("a build sha256 is not 64 lowercase hex characters")
        if not isinstance(file, str) or "\x00" in file:
            raise BadInput("a build file is not a path")
        head, filename = os.path.split(file)
        if head != sdists_root or not SDIST_NAME_RE.fullmatch(filename):
            raise BadInput("a build file is not a source package directly under the sdists folder")
        if (name, version) in seen:
            raise BadInput("a build is listed twice")
        seen.add((name, version))
        if _sha256_of(file, f"source package {filename}") != sha256:
            raise BadInput(f"source package {filename} does not match its sha256")
        builds.append((name, version, file))
    return builds


def _check_backend_wheels(wheels_root: str) -> None:
    """The folder holds exactly the two pinned wheels with their pinned hashes."""
    expected = {
        f"{name}-{version}-py3-none-any.whl": sha256
        for name, (version, sha256) in BUILD_BACKEND_WHEELS.items()
    }
    try:
        present = set(os.listdir(wheels_root))
    except OSError as exc:
        raise BadInput("the build-tool wheels folder is missing") from exc
    if present != set(expected):
        raise BadInput("the build-tool wheels folder does not hold exactly the pinned wheels")
    for filename, sha256 in expected.items():
        if _sha256_of(os.path.join(wheels_root, filename), filename) != sha256:
            raise BadInput(f"build-tool wheel {filename} does not match its pinned sha256")


def _write_constraints(directory: str) -> str:
    resolve._mkdir_fresh(directory)
    path = os.path.join(directory, CONSTRAINTS_NAME)
    lines = "".join(f"{name}=={version}\n" for name, (version, _) in BUILD_BACKEND_WHEELS.items())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as out:
        out.write(lines.encode("ascii"))
    return path


def proof_build_command(
    args: dict[str, Any],
    *,
    deps_root: str = DEPS_ROOT,
    tmp_root: str = resolve.TMP_ROOT,
    runner: resolve.Runner | None = None,
    sdists_root: str = SDISTS_ROOT,
    wheels_root: str = BUILDWHEELS_ROOT,
) -> dict[str, Any]:
    """`proof-build` handler (network OFF; ADR-0041). Args: `builds`.

    Result: `built` (specs) and `failed` (`{spec, reason}`, `build_failed` for a non-zero exit,
    `build_timeout` for a kill at the time limit or a spent budget); every requested spec is in
    exactly one. Build output goes to DEVNULL: it is untrusted and never read or logged.
    """
    builds = _validate_builds(args.get("builds"), sdists_root)
    _check_backend_wheels(wheels_root)
    run = runner or subprocess.run
    constraints = _write_constraints(os.path.join(tmp_root, PIRBUILD_DIR))
    built: list[str] = []
    failed: list[dict[str, str]] = []
    deadline = time.monotonic() + BUILD_BUDGET_S
    for number, (name, version, file) in enumerate(builds):
        spec = f"{name}=={version}"
        remaining = deadline - time.monotonic()
        if remaining < 1:
            failed.append({"spec": spec, "reason": BUILD_TIMEOUT})
            continue
        workdir = os.path.join(tmp_root, f"{UVBUILD_PREFIX}{number}")
        resolve._mkdir_fresh(workdir)  # fresh and empty, outside the repository
        try:
            done = run(
                build_build_argv(file, constraints, deps_root, wheels_root),
                cwd=workdir,
                env=dict(resolve.UV_BUILD_ENV),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=min(BUILD_TIMEOUT_S, remaining),
                check=False,
            )
        except subprocess.TimeoutExpired:
            failed.append({"spec": spec, "reason": BUILD_TIMEOUT})
            continue
        except OSError as exc:
            raise AgentError(f"cannot start uv ({type(exc).__name__})") from exc
        if done.returncode == 0:
            built.append(spec)
        else:
            failed.append({"spec": spec, "reason": BUILD_FAILED})
    log(f"proof-build: {len(built)} built, {len(failed)} failed")
    return {"built": built, "failed": failed}


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
