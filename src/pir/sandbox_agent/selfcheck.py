"""`selfcheck`: fingerprint of the base image the agent runs in (section 7.2, ADR-0033).

Standard library only. The host compares the result with the committed, reviewed fingerprint
(`src/pir/executor/base_image_fingerprint.json`) and refuses to run anything else on a mismatch.
Network OFF, no arguments.
"""

import hashlib
import os
import platform
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from .protocol import BadInput
from .resolve import UV_BIN, UV_ENV

FINGERPRINTED_FILES: tuple[str, ...] = (
    "/usr/local/bin/python3.13",
    "/usr/local/lib/libpython3.13.so.1.0",
    "/usr/local/bin/uv",
    "/usr/local/bin/uvx",
    "/lib64/ld-linux-x86-64.so.2",
    "/lib/x86_64-linux-gnu/libc.so.6",
)
STDLIB_ROOT = "/usr/local/lib/python3.13"
MISSING = "missing"
UNREADABLE = "unreadable"
UV_VERSION_TIMEOUT_S = 30
_CHUNK = 1024 * 1024

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def hash_file(path: str) -> str:
    """Lowercase sha256 hex of the content (links followed), "missing" if there is no such file,
    "unreadable" if it exists but cannot be read."""
    if not os.path.isfile(path):
        return MISSING
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        return UNREADABLE
    return digest.hexdigest()


def _entry(path: str) -> str | None:
    """The tree entry of a path: `link:<target>`, the content hash, or None for anything else."""
    if os.path.islink(path):
        try:
            return "link:" + os.readlink(path)
        except OSError:
            return UNREADABLE
    if os.path.isfile(path):
        digest = hash_file(path)
        return UNREADABLE if digest == MISSING else digest
    return None


def stdlib_tree_sha256(root: str) -> str:
    r"""sha256 over the lines `<relative path>\t<entry>\n`, sorted by relative path.

    One line per regular file and symlink under `root`, walked without following links;
    directories add nothing. Deterministic and independent of the directory listing order.
    """
    lines: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = os.path.join(dirpath, name)
            entry = _entry(path)
            if entry is not None:
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                lines[relative] = entry
    outer = hashlib.sha256()
    for relative in sorted(lines):
        outer.update(f"{relative}\t{lines[relative]}\n".encode("utf-8", errors="surrogateescape"))
    return outer.hexdigest()


def uv_version(runner: Runner = subprocess.run, uv_bin: str = UV_BIN) -> str:
    """Stripped stdout of `uv --version`, spawned with the fixed environment (never os.environ)."""
    try:
        done = runner(
            [uv_bin, "--version"],
            cwd="/",
            env=dict(UV_ENV),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=UV_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNREADABLE
    if done.returncode != 0:
        return UNREADABLE
    return (done.stdout or "").strip()


def compute_selfcheck(
    *,
    files: tuple[str, ...] = FINGERPRINTED_FILES,
    stdlib_root: str = STDLIB_ROOT,
    runner: Runner = subprocess.run,
    uv_bin: str = UV_BIN,
) -> dict[str, Any]:
    """The SelfcheckResult: {python_version, uv_version, files, stdlib_tree_sha256}."""
    return {
        "python_version": platform.python_version(),
        "uv_version": uv_version(runner, uv_bin),
        "files": {path: hash_file(path) for path in files},
        "stdlib_tree_sha256": stdlib_tree_sha256(stdlib_root),
    }


def selfcheck_command(args: Mapping[str, Any]) -> dict[str, Any]:
    """The `selfcheck` command: takes no arguments (`--args-b64 e30=`)."""
    if args:
        raise BadInput("selfcheck takes no arguments")
    return compute_selfcheck(files=FINGERPRINTED_FILES, stdlib_root=STDLIB_ROOT)
