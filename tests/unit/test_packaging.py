"""Packaging and delivery checks: hashed lock, Docker inputs, Sandbox digest, wheel, docs.

ADR-0030, ARCHITECTURE section 18 (with the SECURITY_REVIEW round 1 test cases to carry),
ADR-0033, ADR-0036, AC-39, NFR-08. The fast checks read files
only. The wheel test builds one in a temporary copy of the project (slow).
"""

import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from pir.config import Settings

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
README = ROOT / "README.md"
LICENSE = ROOT / "LICENSE"
LATEST = ROOT / "eval" / "results" / "latest.json"
FINGERPRINT = ROOT / "src" / "pir" / "executor" / "base_image_fingerprint.json"
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")
LOCK_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")
DEP_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")
DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
ALLOWED_COPY_SOURCES = {
    "requirements.lock",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "src",
    "web",
    "demo",
    "eval/results/latest.json",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_dependencies() -> dict[str, str]:
    """Runtime pins from pyproject.toml, canonical name -> exact version."""
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    pins: dict[str, str] = {}
    for requirement in deps:
        match = DEP_PIN.match(requirement)
        assert match, f"runtime dependency is not an exact pin: {requirement}"
        pins[canonical(match.group(1))] = match.group(2)
    return pins


def lock_entries(text: str) -> dict[str, tuple[str, str]]:
    """Canonical name -> (version, block). A block is a pin line plus its indented continuations."""
    entries: dict[str, tuple[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if current is not None:
                version, block = entries[current]
                entries[current] = (version, block + "\n" + line)
            continue
        match = LOCK_LINE.match(line)
        if match is None:
            raise ValueError(f"unexpected line in requirements.lock: {line!r}")
        current = canonical(match.group(1))
        entries[current] = (match.group(2), line)
    return entries


def dockerfile_lines() -> list[str]:
    return [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]


def dockerignore_patterns(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def docker_excluded(path: str, patterns: list[str]) -> bool:
    """Docker's rule for .dockerignore: the last matching pattern decides; `!` re-includes."""
    excluded = False
    for raw in patterns:
        negate = raw.startswith("!")
        pattern = (raw[1:] if negate else raw).rstrip("/")
        if fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern + "/"):
            excluded = not negate
    return excluded


# ADR-0030: the Space installs only from the hashed lock.


def test_adr0030_lock_lists_every_runtime_dependency_at_its_pin_with_hashes():
    entries = lock_entries(LOCK.read_text(encoding="utf-8"))
    pins = runtime_dependencies()
    assert pins, "pyproject.toml declares runtime dependencies"
    for name, version in pins.items():
        assert name in entries, f"{name} is missing from requirements.lock"
        locked_version, block = entries[name]
        assert locked_version == version, f"{name}: lock has {locked_version}, pyproject {version}"
        assert HASH.search(block), f"{name} has no --hash line"


def test_adr0030_every_locked_package_has_a_sha256_hash():
    entries = lock_entries(LOCK.read_text(encoding="utf-8"))
    assert len(entries) > len(runtime_dependencies()), "transitive packages are locked too"
    unhashed = [name for name, (_, block) in entries.items() if not HASH.search(block)]
    assert unhashed == []


def test_adr0030_lock_header_records_the_generating_command():
    header = LOCK.read_text(encoding="utf-8").splitlines()[:3]
    assert any(
        "uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.13 "
        "-o requirements.lock" in line
        for line in header
    )


def test_adr0030_checker_flags_a_package_without_hash():
    entries = lock_entries("widget==1.0 \\\n    # via gadget\n")
    assert entries["widget"][0] == "1.0"
    assert HASH.search(entries["widget"][1]) is None


def test_adr0030_checker_rejects_a_line_that_is_not_a_pin():
    with pytest.raises(ValueError):
        lock_entries("--index-url https://example.invalid/simple\n")


# ADR-0030 and ARCHITECTURE section 18: the Dockerfile.


def test_adr0030_dockerfile_base_is_pinned_by_sha256_digest():
    froms = [line for line in dockerfile_lines() if line.startswith("FROM ")]
    assert len(froms) == 1
    assert re.fullmatch(r"FROM python:3\.13-slim@sha256:[0-9a-f]{64}", froms[0])


def test_adr0030_dockerfile_installs_locked_hashes_before_the_project():
    lines = dockerfile_lines()
    lock_install = "RUN pip install --no-cache-dir --require-hashes -r requirements.lock"
    project_install = "RUN pip install --no-cache-dir --no-deps ."
    assert lock_install in lines and project_install in lines
    assert lines.index(lock_install) < lines.index(project_install)


def test_arch18_dockerfile_runs_as_uid_1000_and_serves_on_7860():
    lines = dockerfile_lines()
    users = [line for line in lines if line.startswith("USER ")]
    assert users and users[-1] == "USER 1000"
    cmd = [line for line in lines if line.startswith("CMD ")]
    assert cmd == ['CMD ["python", "-m", "pir", "serve", "--host", "0.0.0.0", "--port", "7860"]']


def test_arch18_dockerfile_copies_only_the_app_inputs():
    for line in dockerfile_lines():
        if not line.startswith("COPY "):
            continue
        sources = line.split()[1:-1]
        assert sources, line
        assert set(sources) <= ALLOWED_COPY_SOURCES, f"unexpected COPY source in: {line}"


def test_arch18_checker_flags_a_copy_of_secrets_or_eval_results():
    for line in ("COPY . .", "COPY .env ./", "COPY eval/results ./eval/results"):
        sources = line.split()[1:-1]
        assert not set(sources) <= ALLOWED_COPY_SOURCES


def test_arch18_dockerfile_declares_no_secret_values():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"(?im)^(ENV|ARG)\b.*(KEY|TOKEN|SECRET|PASSWORD)", text) is None


# ARCHITECTURE section 18 and SECURITY_REVIEW round 1 (test cases to carry): .dockerignore.


def test_arch18_dockerignore_excludes_secrets_data_and_eval_results():
    patterns = dockerignore_patterns(DOCKERIGNORE.read_text(encoding="utf-8"))
    for entry in (".env", ".env.*", "!.env.example", "data/", "eval/results/", ".venv/", ".git/"):
        assert entry in patterns, entry
    for path in (
        ".env",
        ".env.local",
        ".env.production",
        "data/spend_ledger.json",
        "eval/results/latest.md",
        "eval/results/2026-10-26T120000Z.json",
        ".venv/pyvenv.cfg",
        ".git/config",
    ):
        assert docker_excluded(path, patterns), path


def test_arch18_dockerignore_keeps_example_and_app_inputs():
    patterns = dockerignore_patterns(DOCKERIGNORE.read_text(encoding="utf-8"))
    for path in (".env.example", "src/pir/cli.py", "demo/replays/index.json", "web/index.html"):
        assert not docker_excluded(path, patterns), path


def test_arch18_checker_flags_a_dockerignore_without_env_glob():
    patterns = dockerignore_patterns("data/\neval/results/\n")
    assert not docker_excluded(".env.local", patterns)


# ADR-0036: the image ships exactly one file from eval/results/, latest.json.


def test_adr0036_dockerignore_reincludes_only_latest_json():
    patterns = dockerignore_patterns(DOCKERIGNORE.read_text(encoding="utf-8"))
    reincludes = [entry for entry in patterns if entry.startswith("!eval/results/")]
    assert reincludes == ["!eval/results/latest.json"]
    assert patterns.index("!eval/results/latest.json") == patterns.index("eval/results/") + 1
    assert not docker_excluded("eval/results/latest.json", patterns)
    for path in (
        "eval/results/latest.md",
        "eval/results/2026-10-26T120000Z.json",
        "eval/results/sub/latest.json",
    ):
        assert docker_excluded(path, patterns), path


def test_adr0036_dockerfile_copies_only_latest_json_from_eval():
    lines = dockerfile_lines()
    eval_copies = [line for line in lines if line.startswith("COPY ") and "eval" in line]
    assert eval_copies == ["COPY eval/results/latest.json ./eval/results/latest.json"]
    assert "eval/results/latest.md" not in ALLOWED_COPY_SOURCES
    assert lines.index("COPY demo ./demo") < lines.index(eval_copies[0]) < lines.index("USER 1000")


# ADR-0033: the Sandbox base image keeps its digest; the fingerprint names the same image.


def test_adr0033_sandbox_base_image_default_keeps_its_sha256_digest():
    default = Settings.model_fields["sandbox_base_image"].default
    assert default is not None
    assert DIGEST_REF.fullmatch(default), default
    assert default.startswith("ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:")


def test_adr0033_fingerprint_names_the_same_base_image_as_the_setting():
    default = Settings.model_fields["sandbox_base_image"].default
    fingerprint = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    assert fingerprint["base_image"] == default


# NFR-08 and AC-39: README and LICENSE.


def test_nfr08_license_is_mit_2026_jegadeesh():
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Jegadeesh" in text
    assert "Permission is hereby granted, free of charge" in text


def test_nfr08_readme_has_setup_steps_and_commands():
    text = README.read_text(encoding="utf-8")
    assert 'pip install -e ".[dev]"' in text
    assert 'python -m pytest -q -m "not slow"' in text
    assert "LICENSE" in text


def test_ac39_readme_eval_figures_are_the_ones_in_latest_json():
    summary = json.loads(LATEST.read_text(encoding="utf-8"))
    assert summary["scored"] is True
    text = README.read_text(encoding="utf-8")
    assert f"precision {summary['precision']:.2f}" in text
    assert f"recall {summary['recall']:.2f}" in text
    assert f"{summary['n']} cases" in text


def test_ac39_every_41_to_3_mention_is_labelled_a_motivating_example():
    text = README.read_text(encoding="utf-8")
    mentions = [line for line in text.splitlines() if re.search(r"41\s*(to|→|->)\s*3", line)]
    for line in mentions:
        assert "motivating" in line.lower(), line


def test_ac39_checker_flags_an_unlabelled_41_to_3_claim():
    line = "Results: 41 to 3 reachable."
    assert re.search(r"41\s*(to|→|->)\s*3", line)
    assert "motivating" not in line.lower()


def test_nfr08_readme_has_a_demo_link_placeholder():
    assert "Demo" in README.read_text(encoding="utf-8")


# ADR-0030: the wheel carries the Sandbox fingerprint (slow: builds a wheel).


@pytest.mark.slow
def test_adr0033_wheel_contains_base_image_fingerprint(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(ROOT / name, project / name)
    shutil.copytree(
        ROOT / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")
    )
    out = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "uv", "build", "--wheel", "--out-dir", str(out), str(project)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
    assert "pir/executor/base_image_fingerprint.json" in names
    assert "pir/keepalive.py" in names
