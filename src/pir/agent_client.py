"""SandboxAgentClient (ARCHITECTURE section 7.2): drives our stdlib-only agent through an Executor.

`open_workspace` builds `agent.pyz` at runtime, prepares an image with it (and, for a local
fixture, a trusted tarball), then runs `fetch` or `unpack` and keeps the saved image A.
`resolve` runs `--lockfile-only` offline and, only if the agent says a compile is needed,
`--compile` with network ON in a disposable branch of A. `index` runs offline from A. The result
line is parsed with caps and validated strictly; the output of the Sandbox is untrusted.

Right after the first `prepare` of a process, and before any `fetch` or `unpack`, `selfcheck`
runs with network OFF and its result must equal the committed, reviewed fingerprint of the base
image (ARCHITECTURE section 7.2, ADR-0033); anything else fails closed. The fake executor skips it.
"""

import asyncio
import base64
import binascii
import gzip
import importlib.resources
import io
import json
import logging
import re
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError, field_validator, model_validator

from pir.config import Settings, load_settings
from pir.executor.base import ExecResult, Executor, ExecutorUnavailable, ImageRef
from pir.github import ValidatedTarget
from pir.models import (
    PACKAGE_NAME_RE,
    SHA_RE,
    DependencySource,
    Strict,
    check_repo_path,
    is_pep440,
)
from pir.models.index import AnalysisIndex
from pir.spend import ScanBudget

logger = logging.getLogger("pir.agent_client")

AGENT_PATH = "/opt/pir/agent.pyz"
TARBALL_PATH = "/opt/pir/src.tar.gz"
PYTHON = "/usr/local/bin/python3.13"
RESULT_PREFIX = "PIR-RESULT:"
MAX_RESULT_B64_BYTES = 8388608  # 8 MiB; the same constant as sandbox_agent/protocol.py
MAX_RESULT_JSON_BYTES = 50 * 1024 * 1024
SELFCHECK_TIMEOUT_S = 60
FINGERPRINT_RESOURCE = ("pir.executor", "base_image_fingerprint.json")
SELFCHECK_MISMATCH = "base image does not match the reviewed fingerprint"
_STDERR_TAIL_CHARS = 300
_GROUP_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_SHIM = "import sys\nfrom pir.sandbox_agent.__main__ import run\n\nsys.exit(run())\n"

Step = Literal["selfcheck", "fetch", "unpack", "resolve", "index", "proof-install", "proof-run"]


class SandboxTimeout(Exception):
    """A Sandbox step timed out (`timed_out=True`). Becomes `sandbox_timeout`."""

    def __init__(self, step: str) -> None:
        super().__init__(f"sandbox timeout in {step}")
        self.step = step


class SandboxFailed(Exception):
    """A Sandbox step failed: truncated, missing or invalid result, non-zero exit, unavailable.
    Becomes `sandbox_failed`."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"sandbox failed in {step}: {detail}")
        self.step = step
        self.detail = detail


class LocalFixtureTarget(Strict):
    """A trusted local fixture repository directory (used with the fake executor and in tests)."""

    path: str
    commit_sha: str

    @field_validator("commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not SHA_RE.fullmatch(value):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        return value


class FetchResult(Strict):
    commit_sha: str
    files_total: int
    bytes_total: int
    py_files: int
    manifests: list[str]

    @field_validator("commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not SHA_RE.fullmatch(value):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        return value

    @field_validator("files_total", "bytes_total", "py_files")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("counts must not be negative")
        return value

    @field_validator("manifests")
    @classmethod
    def _manifests(cls, value: list[str]) -> list[str]:
        for name in value:
            check_repo_path(name)
        return value


class ResolvedDependency(Strict):
    """A dependency record as the agent emits it (no OSV fields: the pipeline adds those)."""

    name: str
    source: DependencySource
    groups: list[str]
    version: str | None
    resolved: bool
    unresolved_reason: str | None
    direct: bool | None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if len(value) > 100 or not PACKAGE_NAME_RE.fullmatch(value):
            raise ValueError("name must be a PEP 503 normalized package name")
        return value

    @field_validator("groups")
    @classmethod
    def _groups(cls, value: list[str]) -> list[str]:
        if any(not _GROUP_RE.fullmatch(group) for group in value):
            raise ValueError("invalid dependency group name")
        return value

    @model_validator(mode="after")
    def _check(self) -> "ResolvedDependency":
        if self.resolved:
            if self.version is None or not is_pep440(self.version):
                raise ValueError("a resolved dependency needs a PEP 440 version")
            if self.unresolved_reason is not None:
                raise ValueError("unresolved_reason is set only when not resolved")
        else:
            if self.version is not None:
                raise ValueError("version is None when not resolved")
            if not self.unresolved_reason:
                raise ValueError("an unresolved dependency needs unresolved_reason")
        return self


class ResolveResult(Strict):
    method: Literal["lockfile", "compiled", "failed"]
    lockfile: str | None
    compiled_from: str | None
    python_version: str | None
    dependencies: list[ResolvedDependency]
    notes: list[str]
    needs_compile: bool

    @model_validator(mode="after")
    def _check(self) -> "ResolveResult":
        if (self.method == "lockfile") != (self.lockfile is not None):
            raise ValueError("lockfile is set iff method == 'lockfile'")
        if (self.method == "compiled") != (self.compiled_from is not None):
            raise ValueError("compiled_from is set iff method == 'compiled'")
        for path in (self.lockfile, self.compiled_from):
            if path is not None:
                check_repo_path(path)
        return self


_PIN_RE = re.compile(r"(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)==(?P<version>[A-Za-z0-9.!+_-]{1,64})")
_RECORDED_CALL_RE = re.compile(
    r"(?P<target>[A-Za-z0-9_.]+) called from (?P<file>.+):(?P<line>[0-9]+)"
)
ENTRY_ENDED_REASON = "entry ended the process before the harness reported"


def _check_pins(values: list[str]) -> list[str]:
    for value in values:
        match = _PIN_RE.fullmatch(value)
        if match is None or len(match.group("name")) > 100 or not is_pep440(match.group("version")):
            raise ValueError("a pin must look like name==version")
    return values


class ProofInstallResult(Strict):
    """`proof-install` output: the pins installed from wheels and the pins with no wheel."""

    installed: list[str]
    no_wheel: list[str]

    @field_validator("installed", "no_wheel")
    @classmethod
    def _pins(cls, value: list[str]) -> list[str]:
        return _check_pins(value)


class ProofRunResult(Strict):
    """`proof-run` output (the report's Proof without `ran_at`)."""

    status: Literal["proven", "not_proven"]
    reason: str
    entry: str
    target: str
    recorded_call: str | None

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value or len(value) > 500:
            raise ValueError("reason must be 1 to 500 characters")
        return value

    @model_validator(mode="after")
    def _check(self) -> "ProofRunResult":
        if (self.status == "proven") != (self.recorded_call is not None):
            raise ValueError("recorded_call is set iff proven")
        if self.recorded_call is not None:
            match = _RECORDED_CALL_RE.fullmatch(self.recorded_call)
            if match is None or match.group("target") != self.target:
                raise ValueError("recorded_call must read '<target> called from <file>:<line>'")
            check_repo_path(match.group("file"))
        return self


@dataclass
class ProofImage:
    """Image P (A plus the installed wheels) and what `proof-install` reported."""

    image: ImageRef
    installed: list[str]
    no_wheel: list[str]


@dataclass
class Workspace:
    """A scan's Sandbox state: image A (the repo, saved by fetch or unpack) and its fetch facts."""

    image: ImageRef
    commit_sha: str
    fetch: FetchResult
    budget: ScanBudget
    saved: list[ImageRef] = field(default_factory=list)
    closed: bool = False


# ---- the agent archive --------------------------------------------------------------------


def build_agent_pyz() -> bytes:
    """The zipapp: `pir/sandbox_agent/*.py` plus a root `__main__.py` shim. Deterministic."""
    package_dir = Path(__file__).resolve().parent / "sandbox_agent"
    entries: dict[str, bytes] = {
        "__main__.py": _SHIM.encode("utf-8"),
        "pir/__init__.py": b"",
    }
    for source in sorted(package_dir.glob("*.py")):
        entries[f"pir/sandbox_agent/{source.name}"] = source.read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue()


def build_fixture_tarball(path: str) -> bytes:
    """A gzip tarball of the fixture directory under one top directory (`unpack` strips it)."""
    root = Path(path)
    if not root.is_dir():
        raise SandboxFailed("unpack", "the fixture path is not a directory")

    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        return info

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        tar.add(str(root), arcname="repo", filter=normalize)
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", mtime=0) as handle:
        handle.write(raw.getvalue())
    return packed.getvalue()


# ---- result parsing -----------------------------------------------------------------------


def _printable_tail(text: str) -> str:
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text.strip())
    return cleaned[-_STDERR_TAIL_CHARS:]


def parse_result(step: str, result: ExecResult) -> dict[str, Any]:
    """The agent's JSON object from an `ExecResult`, or SandboxTimeout / SandboxFailed.

    Order: timed out, truncated, non-zero exit, exactly one result line, base64 cap, gzip with
    a decompressed cap, UTF-8 JSON that is an object.
    """
    if result.timed_out:
        raise SandboxTimeout(step)
    if result.truncated:
        raise SandboxFailed(step, "output was truncated")
    if result.exit_code != 0:
        tail = _printable_tail(result.stderr)
        raise SandboxFailed(step, f"exit code {result.exit_code}" + (f": {tail}" if tail else ""))
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith(RESULT_PREFIX)]
    if len(lines) != 1:
        raise SandboxFailed(step, f"expected one result line, found {len(lines)}")
    body = lines[0][len(RESULT_PREFIX) :].strip()
    if len(body) > MAX_RESULT_B64_BYTES:
        raise SandboxFailed(step, "result line is over the 8 MiB base64 cap")
    try:
        packed = base64.b64decode(body.encode("ascii"), validate=True)
        decompressor = zlib.decompressobj(wbits=31)
        raw = decompressor.decompress(packed, MAX_RESULT_JSON_BYTES)
        if decompressor.unconsumed_tail:
            raise SandboxFailed(step, "result is over the 50 MiB decompressed cap")
        if not decompressor.eof:
            raise SandboxFailed(step, "result is an incomplete gzip stream")
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, zlib.error, json.JSONDecodeError) as exc:
        raise SandboxFailed(step, f"result line is invalid ({type(exc).__name__})") from exc
    if not isinstance(payload, dict):
        raise SandboxFailed(step, "result is not a JSON object")
    return payload


def load_fingerprint() -> dict[str, Any]:
    """The committed base-image fingerprint (package data), validated for shape.

    Raises SandboxFailed("selfcheck", ...) when it is missing or malformed (fail closed).
    """
    try:
        package, name = FINGERPRINT_RESOURCE
        data = json.loads(importlib.resources.files(package).joinpath(name).read_text("utf-8"))
    except (OSError, ValueError, ModuleNotFoundError):
        raise SandboxFailed("selfcheck", SELFCHECK_MISMATCH) from None
    ok = (
        isinstance(data, dict)
        and data.get("format") == 1
        and isinstance(data.get("base_image"), str)
        and isinstance(data.get("measured_on"), str)
        and isinstance(data.get("selfcheck"), dict)
    )
    if not ok:
        raise SandboxFailed("selfcheck", SELFCHECK_MISMATCH)
    return data


def _agent_argv(command: str, args: dict[str, Any]) -> list[str]:
    encoded = base64.b64encode(json.dumps(args).encode("utf-8")).decode("ascii")
    return [PYTHON, "-I", "-S", AGENT_PATH, command, "--args-b64", encoded]


# ---- the client ---------------------------------------------------------------------------


class SandboxAgentClient:
    """Raises SandboxTimeout, SandboxFailed, or ExecutorSpendDenied (from the executor)."""

    def __init__(self, executor: Executor, settings: Settings | None = None) -> None:
        self._executor = executor
        self._settings = settings or load_settings()
        self._pyz: bytes | None = None
        self._selfcheck_passed = False
        self._selfcheck_lock = asyncio.Lock()

    def _agent_bytes(self) -> bytes:
        if self._pyz is None:
            self._pyz = build_agent_pyz()
        return self._pyz

    async def _run(
        self,
        step: Step,
        image: ImageRef,
        command: str,
        args: dict[str, Any],
        *,
        network: bool,
        timeout_s: float,
        budget: ScanBudget,
        save: bool = False,
    ) -> ExecResult:
        try:
            return await self._executor.run(
                image,
                _agent_argv(command, args),
                network=network,
                timeout_s=timeout_s,
                save=save,
                budget=budget,
            )
        except ExecutorUnavailable as exc:
            raise SandboxFailed(step, f"sandbox unavailable: {exc}") from exc

    async def open_workspace(
        self, target: ValidatedTarget | LocalFixtureTarget, budget: ScanBudget
    ) -> Workspace:
        settings = self._settings
        limits = {
            "max_bytes": settings.max_repo_mb * 1024 * 1024,
            "max_files": settings.max_repo_files,
        }
        files = {AGENT_PATH: self._agent_bytes()}
        step: Step
        if isinstance(target, ValidatedTarget):
            if self._executor.kind == "fake":
                raise SandboxFailed("fetch", "the fake executor refuses GitHub targets")
            step, command, network = "fetch", "fetch", True
            sha = target.commit_sha
            args: dict[str, Any] = {
                "owner": target.coords.owner,
                "repo": target.coords.name,
                "sha": sha,
                **limits,
            }
            timeout_s = settings.timeout_sandbox_fetch_s
        elif isinstance(target, LocalFixtureTarget):
            step, command, network = "unpack", "unpack", False
            sha = target.commit_sha
            files[TARBALL_PATH] = await asyncio.to_thread(build_fixture_tarball, target.path)
            args = {"sha": sha, **limits}
            timeout_s = settings.timeout_sandbox_fetch_s
        else:
            raise TypeError("target must be a ValidatedTarget or a LocalFixtureTarget")
        try:
            base = await self._executor.prepare(files)
        except ExecutorUnavailable as exc:
            raise SandboxFailed(step, f"sandbox unavailable: {exc}") from exc
        await self._ensure_base_image(base, budget)
        result = await self._run(
            step,
            base,
            command,
            args,
            network=network,
            timeout_s=timeout_s,
            budget=budget,
            save=True,
        )
        saved = result.image
        try:
            payload = parse_result(step, result)
            if saved is None:
                raise SandboxFailed(step, "no image was saved")
            fetched = self._validate(step, FetchResult, payload)
            if fetched.commit_sha != sha:
                raise SandboxFailed(step, "the fetched commit is not the requested commit")
        except (SandboxFailed, SandboxTimeout):
            if saved is not None:
                await self._executor.discard(saved)
            raise
        return Workspace(image=saved, commit_sha=sha, fetch=fetched, budget=budget, saved=[saved])

    async def _ensure_base_image(self, base: ImageRef, budget: ScanBudget) -> None:
        """Run `selfcheck` once per process (ADR-0033); only a pass is remembered.

        Skipped for the fake executor. Raises SandboxFailed("selfcheck", ...) on any difference
        (result, base image, missing or malformed fingerprint), SandboxTimeout("selfcheck") on a
        timeout; ExecutorSpendDenied passes through. Nothing else runs after a failure.
        """
        if self._executor.kind == "fake" or self._selfcheck_passed:
            return
        async with self._selfcheck_lock:
            if self._selfcheck_passed:
                return
            fingerprint = load_fingerprint()
            result = await self._run(
                "selfcheck",
                base,
                "selfcheck",
                {},
                network=False,
                timeout_s=SELFCHECK_TIMEOUT_S,
                budget=budget,
            )
            measured = parse_result("selfcheck", result)
            if (
                fingerprint["base_image"] != self._settings.sandbox_base_image
                or measured != fingerprint["selfcheck"]
            ):
                raise SandboxFailed("selfcheck", SELFCHECK_MISMATCH)
            self._selfcheck_passed = True

    @staticmethod
    def _validate(step: str, model: type[Strict], payload: dict[str, Any]) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise SandboxFailed(step, f"result failed validation at {where or 'root'}") from exc

    async def resolve(self, ws: Workspace) -> ResolveResult:
        settings = self._settings
        python_version = settings.resolve_python_version
        first = await self._run(
            "resolve",
            ws.image,
            "resolve",
            {"mode": "lockfile-only", "python_version": python_version},
            network=False,
            timeout_s=settings.timeout_sandbox_resolve_s,
            budget=ws.budget,
        )
        result = self._validate("resolve", ResolveResult, parse_result("resolve", first))
        if not result.needs_compile:
            return result
        # Compile: network ON, a disposable branch of A (save=False), under its own timeout.
        compiled = await self._run(
            "resolve",
            ws.image,
            "resolve",
            {"mode": "compile", "python_version": python_version},
            network=True,
            timeout_s=settings.timeout_sandbox_compile_s,
            budget=ws.budget,
        )
        return self._validate("resolve", ResolveResult, parse_result("resolve", compiled))

    async def index(self, ws: Workspace, target_modules: list[str]) -> AnalysisIndex:
        result = await self._run(
            "index",
            ws.image,
            "index",
            {"targets": list(target_modules), "sha": ws.commit_sha},
            network=False,
            timeout_s=self._settings.timeout_sandbox_index_s,
            budget=ws.budget,
        )
        index = self._validate("index", AnalysisIndex, parse_result("index", result))
        if index.commit_sha != ws.commit_sha:
            raise SandboxFailed("index", "the index is for a different commit")
        return index

    async def proof_install(self, ws: Workspace, pins: list[tuple[str, str]]) -> ProofImage:
        """Install the pins from wheels only (network ON, from image A, saves P).

        `pins` are `(normalized name, PEP 440 version)` from validated `Dependency` models; the
        agent re-validates them and builds each `name==version` itself. With no pins nothing runs
        and A is P. P is closed with the workspace.
        """
        specs = _check_pins([f"{name}=={version}" for name, version in pins])
        if not specs:
            return ProofImage(image=ws.image, installed=[], no_wheel=[])
        result = await self._run(
            "proof-install",
            ws.image,
            "proof-install",
            {"pins": [{"name": name, "version": version} for name, version in pins]},
            network=True,
            timeout_s=self._settings.timeout_proof_install_s,
            budget=ws.budget,
            save=True,
        )
        if result.image is not None:
            ws.saved.append(result.image)
        installed = self._validate(
            "proof-install", ProofInstallResult, parse_result("proof-install", result)
        )
        if result.image is None:
            raise SandboxFailed("proof-install", "no image was saved")
        if set(installed.installed + installed.no_wheel) != set(specs):
            raise SandboxFailed("proof-install", "the result does not match the requested pins")
        return ProofImage(result.image, installed.installed, installed.no_wheel)

    async def proof_run(
        self,
        ws: Workspace,
        proof_image: ProofImage,
        *,
        entry: str,
        target: str,
        site: str | None = None,
    ) -> ProofRunResult:
        """Run the entry with `target` stubbed (network OFF, from P, nothing saved).

        Raises SandboxTimeout (the platform timeout) or SandboxFailed. A clean exit with no result
        line (repository code ended the process) is a "not proven" result, not a failure.
        """
        args: dict[str, Any] = {"entry": entry, "target": target}
        if site is not None:
            args["site"] = site
        result = await self._run(
            "proof-run",
            proof_image.image,
            "proof-run",
            args,
            network=False,
            timeout_s=self._settings.timeout_proof_run_s,
            budget=ws.budget,
        )
        if (
            result.exit_code == 0
            and not result.timed_out
            and not result.truncated
            and RESULT_PREFIX not in result.stdout
        ):
            return ProofRunResult(
                status="not_proven",
                reason=ENTRY_ENDED_REASON,
                entry=entry,
                target=target,
                recorded_call=None,
            )
        run = self._validate("proof-run", ProofRunResult, parse_result("proof-run", result))
        if (run.entry, run.target) != (entry, target):
            raise SandboxFailed("proof-run", "the result is for a different entry or target")
        return run

    async def close(self, ws: Workspace) -> None:
        """Discard the workspace's saved images. Idempotent; never raises."""
        if ws.closed:
            return
        ws.closed = True
        for image in ws.saved:
            await self._executor.discard(image)
        ws.saved.clear()


__all__ = [
    "ENTRY_ENDED_REASON",
    "FetchResult",
    "LocalFixtureTarget",
    "ProofImage",
    "ProofInstallResult",
    "ProofRunResult",
    "ResolveResult",
    "ResolvedDependency",
    "SandboxAgentClient",
    "SandboxFailed",
    "SandboxTimeout",
    "Workspace",
    "build_agent_pyz",
    "build_fixture_tarball",
    "load_fingerprint",
    "parse_result",
]
