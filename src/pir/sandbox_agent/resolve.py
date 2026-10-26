"""`resolve`: turn the root manifests of a repository into a dependency list (ARCHITECTURE 13).

Standard library only. Repo files are read as data by this module's own code: nothing from the
repository is imported, executed or built, and no repo file is ever a working directory,
configuration or input of a tool. The one tool run, `uv pip compile`, is spawned from a fresh empty
directory, with the fixed environment and the common flags of section 7.2, on a sanitized
requirements file this module writes itself (section 13, rule 5).

Wire shape (arguments travel in `--args-b64`): `{"mode": "lockfile-only" | "compile",
"python_version": "3.12"}`. Result: `{method, lockfile, compiled_from, python_version,
dependencies, notes, needs_compile}`. Each dependency holds the `Dependency` fields that the agent
can know (`name, source, groups, version, resolved, unresolved_reason, direct`); the host adds
`osv_status` and `osv_error`.
"""

import ast
import configparser
import json
import os
import re
import stat
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .protocol import AgentError, BadInput, log, require_str

REPO_ROOT = "/work/repo"
TMP_ROOT = "/tmp"
PIRIN_DIR = "pirin"
UVWD_DIR = "uvwd"
INPUT_NAME = "requirements.in"
OUTPUT_NAME = "requirements.txt"
UV_BIN = "/usr/local/bin/uv"
UV_PYTHON = "/usr/local/bin/python3.13"
UV_INDEX_URL = "https://pypi.org/simple"
UV_TIMEOUT_S = 170
DEFAULT_PYTHON_VERSION = "3.12"

# The fixed environment of section 7.1 (the `env -i` prefix variables); never os.environ.
UV_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "UV_CACHE_DIR": "/tmp/uv",
    "UV_NO_BUILD": "1",
    "UV_NO_CONFIG": "1",
    "UV_PYTHON_DOWNLOADS": "never",
}
# `proof-build` (network OFF, ADR-0041): the same environment without UV_NO_BUILD, plus UV_OFFLINE.
UV_BUILD_ENV: dict[str, str] = {
    **{key: value for key, value in UV_ENV.items() if key != "UV_NO_BUILD"},
    "UV_OFFLINE": "1",
}
UV_COMMON_FLAGS: tuple[str, ...] = (
    "--python",
    UV_PYTHON,
    "--no-build",
    "--no-config",
    "--no-sources",
    "--index-url",
    UV_INDEX_URL,
)

# `proof-build` drops --no-build and --index-url: the build is fed only by local wheels (ADR-0041).
UV_BUILD_COMMON_FLAGS: tuple[str, ...] = (
    "--python",
    UV_PYTHON,
    "--no-config",
    "--no-sources",
)

MAX_MANIFEST_BYTES = 1024 * 1024  # R3-2
MAX_INCLUDE_DEPTH = 5
MAX_INCLUDE_FILES = 20
MAX_COMPILE_OUTPUT_BYTES = 5 * 1024 * 1024
STDERR_TAIL_CHARS = 500
MAX_DEPENDENCIES = 5000

REASON_NON_REGISTRY = "non-registry source"
REASON_DYNAMIC = "dynamic metadata needs a build; not run"
REASON_BAD_VERSION = "not a PEP 440 version"
REASON_BARE_PIPFILE = "Pipfile without Pipfile.lock is not supported"
REASON_UNREADABLE = "compile failed: unreadable manifest"
NOTE_COMPILED = "Versions were compiled with uv pip compile, not locked."
NOTE_RUNTIME_ONLY = (
    "Only runtime dependencies were compiled; optional and development groups are not included."
)
NOTE_REQUIREMENTS_PINS = (
    "requirements.txt pins only the listed packages; transitive dependencies are not included."
)

PEP440_RE = re.compile(  # same pattern as pir.models.PEP440_RE (a test keeps them equal)
    r"v?"
    r"(?:"
    r"(?:[0-9]+!)?"
    r"[0-9]+(?:\.[0-9]+)*"
    r"(?:[-_\.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_\.]?[0-9]*)?"
    r"(?:(?:-[0-9]+)|(?:[-_\.]?(?:post|rev|r)[-_\.]?[0-9]*))?"
    r"(?:[-_\.]?dev[-_\.]?[0-9]*)?"
    r")"
    r"(?:\+[a-z0-9]+(?:[-_\.][a-z0-9]+)*)?",
    re.IGNORECASE,
)
NORMALIZED_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PYTHON_VERSION_RE = re.compile(r"[0-9]{1,2}\.[0-9]{1,2}")

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_EXTRAS = r"\[\s*[A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*\s*\]"
_SPEC = r"(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+"
_MARKER = r"""[A-Za-z0-9_.\s'"()<>=!~,*+-]+"""
ALLOWED_REQUIREMENT_RE = re.compile(
    rf"(?P<name>{_NAME})\s*(?:{_EXTRAS})?\s*(?:{_SPEC}(?:\s*,\s*{_SPEC})*)?\s*(?:;{_MARKER})?"
)
PINNED_REQUIREMENT_RE = re.compile(
    rf"(?P<name>{_NAME})\s*(?:{_EXTRAS})?\s*==\s*(?P<version>[A-Za-z0-9.!+_-]+)\s*(?:;{_MARKER})?"
)
URL_NAMED_RE = re.compile(rf"(?P<name>{_NAME})\s*(?:{_EXTRAS})?\s*@")
EGG_RE = re.compile(rf"[#&]egg=(?P<name>{_NAME})")
OPTION_LONG_RE = re.compile(r"(?P<flag>--[A-Za-z][A-Za-z0-9-]*)(?:[=\s]+(?P<rest>.*))?")
OPTION_SHORT_RE = re.compile(r"(?P<flag>-[A-Za-z])\s*=?\s*(?P<rest>.*)")
PER_REQUIREMENT_OPTION_RE = re.compile(r"\s--[A-Za-z]")
LOWER_BOUND_RE = re.compile(r">=\s*([0-9]+)\.([0-9]+)")
# Keys only on the cause and hint sentences that uv 0.9.30 (the Sandbox image, authoritative) and
# uv 0.13.5 (development) both print; their line wrapping differs, so whitespace is flexible.
BUILD_NEEDED_RE = re.compile(
    r"has\s+no\s+usable\s+wheels|wheels\s+are\s+required\s+for"
    r"|building\s+from\s+source\s+is\s+disabled",
    re.IGNORECASE,
)
UV_OUTPUT_LINE_RE = re.compile(rf"(?P<name>{_NAME})==(?P<version>[A-Za-z0-9.!+_-]+)\s*(?:;.*)?")
INCLUDE_FLAGS = ("-r", "--requirement")
EDITABLE_FLAGS = ("-e", "--editable")
VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")
PATH_PREFIXES = ("./", "../", "/", "~", ".\\", "..\\", "file:")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ManifestError(Exception):
    """A manifest could not be read or parsed (too big, not a regular file, outside the repo...)."""

    def __init__(self, file: str, detail: str) -> None:
        super().__init__(f"{file}: {detail}")
        self.file = file


@dataclass
class Collected:
    """What a requirements file (with its in-repo `-r` includes) declares."""

    requirements: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)  # flags of dropped option lines
    refused: list[str] = field(default_factory=list)  # include targets that were not followed
    editable_targets: list[str] = field(default_factory=list)


@dataclass
class Declared:
    """A compile candidate: the manifest, its requirement strings and what it dropped."""

    file: str
    collected: Collected
    requires_python: str | None = None


@dataclass
class Sanitized:
    lines: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # names of non-registry requirements
    notes: list[str] = field(default_factory=list)


# --- small helpers ---


def normalize_name(raw: object) -> str | None:
    """PEP 503 normalization, then the Dependency name regex; None when the name is unusable."""
    if not isinstance(raw, str):
        return None
    name = re.sub(r"[-_.]+", "-", raw.strip()).lower()
    if len(name) > 100 or not NORMALIZED_NAME_RE.fullmatch(name):
        return None
    return name


def is_pep440(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 64 and PEP440_RE.fullmatch(value) is not None


def _dep(
    name: str,
    source: str,
    version: str | None,
    groups: list[str] | None = None,
    direct: bool | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """One dependency record. A version that is missing or not PEP 440 makes it unresolved."""
    if reason is None and not is_pep440(version):
        reason = REASON_BAD_VERSION
    resolved = reason is None
    return {
        "name": name,
        "source": source,
        "groups": sorted(set(groups or ["main"])),
        "version": version if resolved else None,
        "resolved": resolved,
        "unresolved_reason": reason,
        "direct": direct,
    }


def _unresolved(name: str, reason: str, direct: bool | None = True) -> dict[str, Any]:
    return _dep(name, "manifest", None, ["main"], direct, reason)


def _merge(deps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per (name, version, reason); groups are united; sorted by name then version."""
    merged: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for dep in deps:
        key = (dep["name"], dep["version"], dep["unresolved_reason"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(dep)
            continue
        existing["groups"] = sorted(set(existing["groups"]) | set(dep["groups"]))
        if existing["direct"] is not True:
            existing["direct"] = dep["direct"] if dep["direct"] is not None else existing["direct"]
    return sorted(merged.values(), key=lambda d: (d["name"], d["version"] or ""))


def _result(
    method: str,
    deps: list[dict[str, Any]],
    notes: list[str],
    lockfile: str | None = None,
    compiled_from: str | None = None,
    python_version: str | None = None,
    needs_compile: bool = False,
) -> dict[str, Any]:
    unique_notes = list(dict.fromkeys(notes))
    return {
        "method": method,
        "lockfile": lockfile,
        "compiled_from": compiled_from,
        "python_version": python_version,
        "dependencies": _merge(deps)[:MAX_DEPENDENCIES],
        "notes": unique_notes,
        "needs_compile": needs_compile,
    }


# --- reading repo files as data (R3-1, R3-2) ---


def _inside(root: str, path: str) -> bool:
    real_root = os.path.normcase(os.path.realpath(root))
    real = os.path.normcase(path)
    return real.startswith(real_root.rstrip(os.sep) + os.sep)


def read_repo_file(path: str, repo_root: str, label: str) -> str:
    """Read a manifest as UTF-8 text. It must be a regular file whose real path lies under the repo
    root (R3-1: no `..`, absolute path or symlink escapes) and at most about 1 MiB (R3-2)."""
    real = os.path.realpath(path)
    if not _inside(repo_root, real):
        raise ManifestError(label, "path resolves outside the repository")
    try:
        info = os.stat(real)
        if not stat.S_ISREG(info.st_mode):
            raise ManifestError(label, "not a regular file")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise ManifestError(label, "manifest over the size cap")
        with open(real, "rb") as handle:
            data = handle.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise ManifestError(label, f"unreadable ({type(exc).__name__})") from exc
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError(label, "manifest over the size cap")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(label, "not UTF-8") from exc


def _parse(label: str, parser: Callable[[str], Any], text: str) -> Any:
    """Run a parser on untrusted text; any exception (RecursionError...) means unreadable."""
    try:
        return parser(text)
    except Exception as exc:  # R3-2: every parse exception (RecursionError...) is one reason
        log(f"{label}: parse failed: {type(exc).__name__}")
        raise ManifestError(label, f"parse failed ({type(exc).__name__})") from exc


def load_toml(text: str, label: str) -> dict[str, Any]:
    data = _parse(label, tomllib.loads, text)
    return data if isinstance(data, dict) else {}


def _table(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _file_exists(repo_root: str, name: str) -> bool:
    return os.path.isfile(os.path.join(repo_root, name))


# --- lockfiles (rules 1 to 3) ---


def _names(items: object) -> list[str]:
    """Normalized names from a list of `{name = ...}` tables or requirement strings."""
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items:
        raw = item.get("name") if isinstance(item, dict) else item
        if isinstance(raw, str) and isinstance(item, str):
            match = ALLOWED_REQUIREMENT_RE.match(raw.strip())
            raw = match.group("name") if match else None
        name = normalize_name(raw)
        if name:
            out.append(name)
    return out


def _group_name(raw: object) -> str | None:
    return normalize_name(raw)


def parse_uv_lock(text: str) -> list[dict[str, Any]]:
    data = load_toml(text, "uv.lock")
    packages = _dicts(data.get("package"))
    root = next(
        (
            p
            for p in packages
            if isinstance(p.get("source"), dict)
            and (p["source"].get("virtual") == "." or p["source"].get("editable") == ".")
        ),
        None,
    )
    seeds: dict[str, list[str]] = {}
    if root is not None:
        seeds["main"] = _names(root.get("dependencies"))
        for table in ("optional-dependencies", "dev-dependencies"):
            groups = root.get(table)
            for group, items in groups.items() if isinstance(groups, dict) else []:
                gname = _group_name(group)
                if gname:
                    seeds.setdefault(gname, []).extend(_names(items))
    graph = {
        name: _names(p.get("dependencies"))
        for p in packages
        if p is not root and (name := normalize_name(p.get("name")))
    }
    membership: dict[str, set[str]] = {}
    for group, starts in seeds.items():
        stack, seen = list(starts), set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            membership.setdefault(current, set()).add(group)
            stack.extend(graph.get(current, []))
    direct_names = {n for starts in seeds.values() for n in starts}
    deps: list[dict[str, Any]] = []
    for package in packages:
        name = normalize_name(package.get("name"))
        if package is root or name is None:
            continue
        groups = sorted(membership.get(name, {"main"}))
        direct = (name in direct_names) if root is not None else None
        source = package.get("source")
        if not (isinstance(source, dict) and "registry" in source):
            deps.append(_dep(name, "uv.lock", None, groups, direct, REASON_NON_REGISTRY))
            continue
        version = package.get("version")
        deps.append(
            _dep(name, "uv.lock", version if isinstance(version, str) else None, groups, direct)
        )
    return deps


def _poetry_direct(repo_root: str) -> set[str] | None:
    """Names declared in pyproject.toml, or None when it is missing or unreadable."""
    if not _file_exists(repo_root, "pyproject.toml"):
        return None
    try:
        data = load_toml(
            read_repo_file(os.path.join(repo_root, "pyproject.toml"), repo_root, "pyproject.toml"),
            "pyproject.toml",
        )
    except ManifestError:
        return None
    project = _table(data.get("project"))
    names: set[str] = set(_names(project.get("dependencies")))
    for items in _table(project.get("optional-dependencies")).values():
        names.update(_names(items))
    poetry = _table(_table(data.get("tool")).get("poetry"))
    tables: list[object] = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
    for group in _table(poetry.get("group")).values():
        tables.append(_table(group).get("dependencies"))
    for table in tables:
        if isinstance(table, dict):
            names.update(n for key in table if key != "python" and (n := normalize_name(key)))
    return names


def parse_poetry_lock(text: str, repo_root: str) -> list[dict[str, Any]]:
    data = load_toml(text, "poetry.lock")
    direct_names = _poetry_direct(repo_root)
    deps: list[dict[str, Any]] = []
    for package in _dicts(data.get("package")):
        name = normalize_name(package.get("name"))
        if name is None:
            continue
        raw_groups = package.get("groups")
        if not isinstance(raw_groups, list):
            raw_groups = [package.get("category", "main")]
        groups = [g for g in (_group_name(x) for x in raw_groups) if g] or ["main"]
        direct = None if direct_names is None else name in direct_names
        source = package.get("source")
        if isinstance(source, dict) and source.get("type") in {"git", "directory", "file", "url"}:
            deps.append(_dep(name, "poetry.lock", None, groups, direct, REASON_NON_REGISTRY))
            continue
        version = package.get("version")
        deps.append(
            _dep(name, "poetry.lock", version if isinstance(version, str) else None, groups, direct)
        )
    return deps


def _pipfile_names(repo_root: str) -> set[str] | None:
    if not _file_exists(repo_root, "Pipfile"):
        return None
    try:
        data = load_toml(
            read_repo_file(os.path.join(repo_root, "Pipfile"), repo_root, "Pipfile"), "Pipfile"
        )
    except ManifestError:
        return None
    names: set[str] = set()
    for table in ("packages", "dev-packages"):
        section = data.get(table)
        if isinstance(section, dict):
            names.update(n for key in section if (n := normalize_name(key)))
    return names


def parse_pipfile_lock(text: str, repo_root: str) -> list[dict[str, Any]]:
    data = _parse("Pipfile.lock", json.loads, text)
    if not isinstance(data, dict):
        raise ManifestError("Pipfile.lock", "not a JSON object")
    direct_names = _pipfile_names(repo_root)
    deps: list[dict[str, Any]] = []
    for section, group in (("default", "main"), ("develop", "develop")):
        entries = data.get(section)
        for key, entry in entries.items() if isinstance(entries, dict) else []:
            name = normalize_name(key)
            if name is None or not isinstance(entry, dict):
                continue
            direct = None if direct_names is None else name in direct_names
            if any(k in entry for k in ("git", "path", "file", "editable", "ref")):
                deps.append(_dep(name, "Pipfile.lock", None, [group], direct, REASON_NON_REGISTRY))
                continue
            raw = entry.get("version")
            version = raw[2:].strip() if isinstance(raw, str) and raw.startswith("==") else None
            deps.append(_dep(name, "Pipfile.lock", version, [group], direct))
    return deps


# --- requirements files (rule 4 and the compile input) ---


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            buffer += raw[:-1] + " "
            continue
        lines.append(buffer + raw)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


def _split_option(line: str) -> tuple[str, str]:
    """(flag, rest) of an option line; flag is "" when it is not a recognisable option."""
    match = (
        OPTION_LONG_RE.fullmatch(line) if line.startswith("--") else OPTION_SHORT_RE.fullmatch(line)
    )
    if not match:
        return "", ""
    return match.group("flag"), (match.group("rest") or "").strip()


def collect_requirements(
    path: str, repo_root: str, label: str, depth: int = 0, seen: set[str] | None = None
) -> Collected:
    """Requirements of a file, following `-r` includes that stay inside the repo (depth <= 5).

    A root file that cannot be read raises ManifestError; an include that is refused, unreadable,
    too deep or repeated is recorded in `refused` and not followed.
    """
    seen = set() if seen is None else seen
    seen.add(os.path.normcase(os.path.realpath(path)))
    collected = Collected()
    for logical in _logical_lines(read_repo_file(path, repo_root, label)):
        line = re.sub(r"(?:^|\s)#.*$", "", logical).strip()
        if not line:
            continue
        if line.startswith("-"):
            flag, rest = _split_option(line)
            if flag in INCLUDE_FLAGS:
                _follow_include(rest, path, repo_root, depth, seen, collected)
            elif flag in EDITABLE_FLAGS:
                collected.options.append(flag)
                collected.editable_targets.append(rest)
            else:
                collected.options.append(flag or "(unrecognized)")
            continue
        collected.requirements.append(PER_REQUIREMENT_OPTION_RE.split(line, maxsplit=1)[0].strip())
    return collected


def _follow_include(
    target: str, parent: str, repo_root: str, depth: int, seen: set[str], into: Collected
) -> None:
    refused = "-r"
    if not target or depth + 1 > MAX_INCLUDE_DEPTH or len(seen) >= MAX_INCLUDE_FILES:
        into.refused.append(refused)
        return
    candidate = os.path.join(os.path.dirname(parent), target)
    real = os.path.realpath(candidate)  # R3-1: judged by real path, never by the spelled path
    if os.path.normcase(real) in seen:
        return  # already read (a repeated or circular include)
    if not _inside(repo_root, real):
        into.refused.append(refused)
        return
    try:
        child = collect_requirements(real, repo_root, "include", depth + 1, seen)
    except ManifestError as exc:
        log(f"include not followed: {exc.args[0] if exc.args else 'unreadable'}")
        into.refused.append(refused)
        return
    into.requirements.extend(child.requirements)
    into.options.extend(child.options)
    into.refused.extend(child.refused)
    into.editable_targets.extend(child.editable_targets)


def pinned_requirements(collected: Collected) -> list[tuple[str, str]] | None:
    """(name, version) pairs when every requirement is `name==version`, else None (rule 4)."""
    if not collected.requirements or collected.refused or collected.editable_targets:
        return None
    pairs: list[tuple[str, str]] = []
    for requirement in collected.requirements:
        match = PINNED_REQUIREMENT_RE.fullmatch(requirement)
        name = normalize_name(match.group("name")) if match else None
        if match is None or name is None:
            return None
        pairs.append((name, match.group("version")))
    return pairs


def _option_notes(collected: Collected) -> list[str]:
    notes = [f"ignored option {flag}" for flag in collected.options]
    notes += [
        "ignored option -r (include outside the repository, unreadable or too deep)"
        for _ in collected.refused
    ]
    return list(dict.fromkeys(notes))


# --- sanitizing the compile input (rule 5) ---


def _is_non_registry(requirement: str) -> bool:
    lowered = requirement.lower()
    return (
        "://" in requirement
        or lowered.startswith(VCS_PREFIXES + PATH_PREFIXES)
        or requirement in (".", "..")
        or URL_NAMED_RE.match(requirement) is not None
    )


def _non_registry_name(requirement: str) -> str | None:
    match = URL_NAMED_RE.match(requirement) or EGG_RE.search(requirement)
    return normalize_name(match.group("name")) if match else None


def sanitize(collected: Collected) -> Sanitized:
    """Keep only allowlisted registry requirements; report everything else (rule 5)."""
    result = Sanitized(notes=_option_notes(collected))
    lines: dict[str, None] = {}
    names: dict[str, None] = {}
    unresolved: dict[str, None] = {}
    unnamed = False
    for requirement in collected.requirements:
        if len(requirement) > 300 or not requirement.isprintable():
            unnamed = True
            continue
        if _is_non_registry(requirement):
            name = _non_registry_name(requirement)
            if name:
                unresolved[name] = None
            else:
                unnamed = True
            continue
        match = ALLOWED_REQUIREMENT_RE.fullmatch(requirement)
        name = normalize_name(match.group("name")) if match else None
        if match is None or name is None:
            unnamed = True
            continue
        lines[requirement] = None
        names[name] = None
    for target in collected.editable_targets:
        name = _non_registry_name(target)
        if name:
            unresolved[name] = None
    if unnamed:
        result.notes.append("ignored requirement lines that are not plain registry requirements")
    result.lines = list(lines)
    result.names = list(names)
    result.unresolved = [n for n in unresolved if n not in names]
    return result


def _requirements_from_pyproject(data: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    """(requirement strings, requires-python) of a static `[project]` table, else (None, None)."""
    project = data.get("project")
    if not isinstance(project, dict):
        return None, None
    dynamic = project.get("dynamic")
    if isinstance(dynamic, list) and "dependencies" in dynamic:
        return None, None
    deps = project.get("dependencies")
    requirements = [d.strip() for d in deps if isinstance(d, str)] if isinstance(deps, list) else []
    requires = project.get("requires-python")
    return requirements, requires if isinstance(requires, str) else None


def choose_python_version(requested: str, requires_python: str | None) -> str:
    """The configured target, raised to the lowest version `requires-python` allows if needed."""
    if requires_python:
        match = LOWER_BOUND_RE.search(requires_python)
        if match:
            lowest = (int(match.group(1)), int(match.group(2)))
            wanted = tuple(int(part) for part in requested.split("."))
            if lowest > wanted:
                return f"{lowest[0]}.{lowest[1]}"
    return requested


# --- dynamic metadata (declared names without running anything) ---


def _setup_cfg_names(repo_root: str) -> list[str]:
    try:
        text = read_repo_file(os.path.join(repo_root, "setup.cfg"), repo_root, "setup.cfg")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        _parse("setup.cfg", parser.read_string, text)
        raw = parser.get("options", "install_requires", fallback="")
    except (ManifestError, configparser.Error) as exc:
        log(f"setup.cfg names unknown: {type(exc).__name__}")
        return []
    return [n for line in raw.splitlines() if (n := _requirement_name(line))]


def _requirement_name(requirement: str) -> str | None:
    match = ALLOWED_REQUIREMENT_RE.match(requirement.strip())
    return normalize_name(match.group("name")) if match else None


def _setup_py_names(repo_root: str) -> list[str]:
    """Literal `install_requires=[...]` of a `setup(...)` call, read with ast only (never run)."""
    try:
        text = read_repo_file(os.path.join(repo_root, "setup.py"), repo_root, "setup.py")
        tree = _parse("setup.py", ast.parse, text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called != "setup":
                continue
            for keyword in node.keywords:
                if keyword.arg == "install_requires":
                    value = ast.literal_eval(keyword.value)
                    items = value if isinstance(value, list | tuple) else []
                    return [n for item in items if (n := _requirement_name(str(item)))]
    except (ManifestError, ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        log(f"setup.py names unknown: {type(exc).__name__}")
    return []


def _pyproject_names(repo_root: str, data: dict[str, Any]) -> list[str]:
    names = _names(_table(data.get("project")).get("dependencies"))
    poetry = _table(_table(data.get("tool")).get("poetry")).get("dependencies")
    if isinstance(poetry, dict):
        names += [n for key in poetry if key != "python" and (n := normalize_name(key))]
    return names


# --- compile ---


def _mkdir_fresh(path: str) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError as exc:
        raise AgentError(f"{path} already exists; refusing to reuse it") from exc


def _printable_tail(text: str, limit: int) -> str:
    tail = text[-limit:]
    return "".join(ch if ch.isprintable() or ch == "\n" else " " for ch in tail).strip()


def _failed_deps(san: Sanitized, reason: str) -> list[dict[str, Any]]:
    deps = [_unresolved(name, reason) for name in san.names]
    deps += [_unresolved(name, REASON_NON_REGISTRY) for name in san.unresolved]
    return deps


def build_uv_argv(input_path: str, python_version: str, output_path: str) -> list[str]:
    return [
        UV_BIN,
        "pip",
        "compile",
        input_path,
        *UV_COMMON_FLAGS,
        "--python-version",
        python_version,
        "--no-header",
        "--no-annotate",
        "--quiet",
        "--output-file",
        output_path,
    ]


def _compiled_output(output_path: str) -> str:
    """The text of uv's `--output-file`, or "" when it is missing, not a file or over the cap.

    Only this file is read (ADR-0034): stdout is ignored and a file named `-` is never looked at.
    """
    try:
        if not os.path.isfile(output_path) or os.path.islink(output_path):
            return ""
        if os.path.getsize(output_path) > MAX_COMPILE_OUTPUT_BYTES:
            return ""
        with open(output_path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def run_compile(
    declared: Declared,
    san: Sanitized,
    python_version: str,
    tmp_root: str,
    runner: Runner,
) -> dict[str, Any]:
    """Write the sanitized input and run `uv pip compile` from a fresh empty directory."""
    notes = list(san.notes)
    source_file = declared.file
    if not san.lines:  # nothing registry-based to compile: no uv call
        return _result(
            "compiled",
            [_unresolved(name, REASON_NON_REGISTRY) for name in san.unresolved],
            notes + ["No registry requirements were found to compile."],
            compiled_from=source_file,
            python_version=python_version,
        )
    pirin = os.path.join(tmp_root, PIRIN_DIR)
    uvwd = os.path.join(tmp_root, UVWD_DIR)
    _mkdir_fresh(pirin)
    _mkdir_fresh(uvwd)
    input_path = os.path.join(pirin, INPUT_NAME)
    with open(input_path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(san.lines) + "\n")
    output_path = os.path.join(pirin, OUTPUT_NAME)
    argv = build_uv_argv(input_path, python_version, output_path)
    try:
        done = runner(
            argv,
            cwd=uvwd,
            env=dict(UV_ENV),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=UV_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        reason = f"compile failed: uv timed out after {UV_TIMEOUT_S} s"
        return _result("failed", _failed_deps(san, reason), notes + [reason])
    except OSError as exc:
        reason = f"compile failed: cannot start uv ({type(exc).__name__})"
        return _result("failed", _failed_deps(san, reason), notes + [reason])
    stderr = done.stderr or ""
    if done.returncode != 0:
        if BUILD_NEEDED_RE.search(stderr):
            log("uv would need a build; output discarded")
            return _result("failed", _failed_deps(san, REASON_DYNAMIC), notes + [REASON_DYNAMIC])
        reason = f"compile failed: {_printable_tail(stderr, STDERR_TAIL_CHARS)}"
        return _result("failed", _failed_deps(san, reason), notes + [reason])
    direct = set(san.names)
    deps: list[dict[str, Any]] = []
    for line in _compiled_output(output_path).splitlines():
        match = UV_OUTPUT_LINE_RE.fullmatch(line.strip())
        name = normalize_name(match.group("name")) if match else None
        if match and name:
            deps.append(
                _dep(name, "uv pip compile", match.group("version"), ["main"], name in direct)
            )
    if not deps:
        reason = "compile failed: no output"
        return _result("failed", _failed_deps(san, reason), notes + [reason])
    deps += [_unresolved(name, REASON_NON_REGISTRY) for name in san.unresolved]
    return _result(
        "compiled",
        deps,
        notes + [NOTE_COMPILED, NOTE_RUNTIME_ONLY],
        compiled_from=source_file,
        python_version=python_version,
    )


# --- the resolution order ---


def _candidates(repo_root: str, pinned_file: Collected | None) -> list[Declared]:
    """Compile candidates in order: static pyproject.toml, requirements.in, requirements.txt."""
    found: list[Declared] = []
    if _file_exists(repo_root, "pyproject.toml"):
        path = os.path.join(repo_root, "pyproject.toml")
        data = load_toml(read_repo_file(path, repo_root, "pyproject.toml"), "pyproject.toml")
        requirements, requires = _requirements_from_pyproject(data)
        if requirements is not None:
            found.append(Declared("pyproject.toml", Collected(requirements=requirements), requires))
    if _file_exists(repo_root, "requirements.in"):
        path = os.path.join(repo_root, "requirements.in")
        found.append(
            Declared("requirements.in", collect_requirements(path, repo_root, "requirements.in"))
        )
    if pinned_file is not None:
        found.append(Declared("requirements.txt", pinned_file))
    return found


def _dynamic_result(repo_root: str) -> dict[str, Any] | None:
    """Rule 5/6 for manifests that need a build: declared names unresolved, nothing run."""
    names: list[str] = []
    seen_any = False
    if _file_exists(repo_root, "pyproject.toml"):
        seen_any = True
        path = os.path.join(repo_root, "pyproject.toml")
        data = load_toml(read_repo_file(path, repo_root, "pyproject.toml"), "pyproject.toml")
        names += _pyproject_names(repo_root, data)
    if _file_exists(repo_root, "setup.cfg"):
        seen_any = True
        names += _setup_cfg_names(repo_root)
    if _file_exists(repo_root, "setup.py"):
        seen_any = True
        names += _setup_py_names(repo_root)
    if not seen_any:
        return None
    deps = [_unresolved(name, REASON_DYNAMIC) for name in dict.fromkeys(names)]
    return _result("failed", deps, [REASON_DYNAMIC])


def _bare_pipfile_result(repo_root: str) -> dict[str, Any]:
    names = sorted(_pipfile_names(repo_root) or [])
    deps = [_unresolved(name, REASON_BARE_PIPFILE) for name in names]
    return _result("failed", deps, [REASON_BARE_PIPFILE])


def _lockfile_result(lockfile: str, deps: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    return _result("lockfile", deps, notes, lockfile=lockfile)


def resolve_manifests(
    repo_root: str,
    compile_allowed: bool,
    python_version: str,
    tmp_root: str = TMP_ROOT,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """The resolution order of section 13. With `compile_allowed` false, a compile that would be
    needed is reported as `needs_compile` instead of being run."""
    try:
        return _resolve(repo_root, compile_allowed, python_version, tmp_root, runner)
    except ManifestError as exc:
        log(f"unreadable manifest: {exc}")
        return _result("failed", [], [f"{exc.file}: unreadable manifest", REASON_UNREADABLE])


def _resolve(
    repo_root: str,
    compile_allowed: bool,
    python_version: str,
    tmp_root: str,
    runner: Runner | None,
) -> dict[str, Any]:
    for name, parse in (
        ("uv.lock", lambda text: parse_uv_lock(text)),
        ("poetry.lock", lambda text: parse_poetry_lock(text, repo_root)),
        ("Pipfile.lock", lambda text: parse_pipfile_lock(text, repo_root)),
    ):
        if _file_exists(repo_root, name):
            text = read_repo_file(os.path.join(repo_root, name), repo_root, name)
            return _lockfile_result(name, parse(text), [])
    unpinned: Collected | None = None
    if _file_exists(repo_root, "requirements.txt"):
        path = os.path.join(repo_root, "requirements.txt")
        collected = collect_requirements(path, repo_root, "requirements.txt")
        pairs = pinned_requirements(collected)
        if pairs is not None:
            deps = [_dep(n, "requirements.txt", v, ["main"], True) for n, v in pairs]
            return _lockfile_result(
                "requirements.txt", deps, _option_notes(collected) + [NOTE_REQUIREMENTS_PINS]
            )
        unpinned = collected
    empty: Declared | None = None
    for declared in _candidates(repo_root, unpinned):
        san = sanitize(declared.collected)
        if not san.lines and not san.unresolved:
            empty = empty or declared
            continue
        version = choose_python_version(python_version, declared.requires_python)
        if not compile_allowed:
            return _result("failed", [], [], needs_compile=True)
        return run_compile(declared, san, version, tmp_root, runner or subprocess.run)
    if empty is not None:
        return _result(
            "compiled",
            [],
            ["No runtime dependencies are declared."],
            compiled_from=empty.file,
            python_version=python_version,
        )
    dynamic = _dynamic_result(repo_root)
    if dynamic is not None:
        return dynamic
    if _file_exists(repo_root, "Pipfile"):
        return _bare_pipfile_result(repo_root)
    return _result("failed", [], ["No supported dependency manifest was found."])


# --- command ---


def resolve_command(
    args: dict[str, Any],
    repo_root: str = REPO_ROOT,
    tmp_root: str = TMP_ROOT,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """`resolve` handler. Args: `mode` ("lockfile-only" or "compile") and `python_version`."""
    mode = require_str(args, "mode")
    if mode not in ("lockfile-only", "compile"):
        raise BadInput("mode must be 'lockfile-only' or 'compile'")
    python_version = args.get("python_version", DEFAULT_PYTHON_VERSION)
    if not isinstance(python_version, str) or not PYTHON_VERSION_RE.fullmatch(python_version):
        raise BadInput("python_version must look like 3.12")
    if not os.path.isdir(repo_root):
        raise BadInput("repository directory does not exist")
    result = resolve_manifests(repo_root, mode == "compile", python_version, tmp_root, runner)
    log(f"resolved: method={result['method']} dependencies={len(result['dependencies'])}")
    return result
