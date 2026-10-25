"""The committed eval fixtures and the cases loader (AC-33, AC-39)."""

import re
from pathlib import Path

import pytest

from pir.evaluation.cases import CasesError, load_cases
from pir.models.eval import EvalRunSummary

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "eval" / "cases.toml"
SHA_SOURCE = re.compile(r"github:[^@]+@[0-9a-f]{40}")
OSV_ID = re.compile(r"[A-Z][A-Z0-9]*-[A-Za-z0-9._-]+")


def labels(suite) -> dict[str, int]:
    out: dict[str, int] = {}
    for case in suite.cases:
        out[case.label] = out.get(case.label, 0) + 1
    return out


def test_ac33_committed_cases_meet_the_plan_counts():
    suite = load_cases(CASES)
    counts = labels(suite)

    assert len(suite.cases) >= 12  # plan; AC-33 needs 10
    assert counts["likely reachable"] >= 4
    assert counts["no reachable path detected"] >= 3
    assert counts["uncertain"] >= 3


def test_ac33_every_case_names_a_defined_repo_and_a_valid_osv_id():
    suite = load_cases(CASES)
    repo_ids = {r.id for r in suite.repos}

    assert len({c.id for c in suite.cases}) == len(suite.cases)
    for case in suite.cases:
        assert case.repo in repo_ids, case.id
        assert OSV_ID.fullmatch(case.advisory), case.id
        assert case.rationale.strip(), case.id


def test_ac33_github_sources_are_pinned_to_a_40_hex_sha():
    suite = load_cases(CASES)
    github = [r for r in suite.repos if r.source.startswith("github:")]

    assert len(github) >= 2
    for repo in github:
        assert SHA_SOURCE.fullmatch(repo.source), repo.id


def test_ac33_local_fixtures_exist_and_pin_the_case_package():
    suite = load_cases(CASES)
    local = [r for r in suite.repos if r.source.startswith("local:")]

    assert len(local) >= 5
    for repo in local:
        directory = suite.local_path(repo)
        pins = (directory / "requirements.txt").read_text(encoding="utf-8").lower()
        assert re.search(r"^[a-z0-9._-]+==\S+$", pins, re.MULTILINE), repo.id
        for case in (c for c in suite.cases if c.repo == repo.id):
            assert case.package.lower() in pins, f"{case.id}: {case.package} is not pinned"


def test_ac33_fixtures_cover_every_pattern_of_section_15():
    suite = load_cases(CASES)
    source = "\n".join(
        p.read_text(encoding="utf-8")
        for r in suite.repos
        if r.source.startswith("local:")
        for p in suite.local_path(r).rglob("*.py")
    )

    assert "yaml.full_load(" in source  # direct call
    assert "import yaml as y" in source  # import m as a
    assert "from yaml import full_load as" in source  # from m import f as g
    assert "from shipper.parser import" in source  # call through repo functions from a CLI
    assert "yaml.safe_dump(" in source  # imported, vulnerable function never called
    assert "getattr(yaml" in source  # dynamic use
    never = suite.local_path(suite.repo("yaml-not-imported"))
    assert not any("import yaml" in p.read_text(encoding="utf-8") for p in never.rglob("*.py"))


def test_ac39_published_latest_json_validates():
    summary = EvalRunSummary.model_validate_json(
        (ROOT / "eval" / "results" / "latest.json").read_text(encoding="utf-8")
    )

    assert summary.bar == 0.8
    assert 0 <= summary.abstention_rate <= 1


def test_ac39_readme_makes_no_unlabelled_41_to_3_claim():
    readme = ROOT / "README.md"
    if not readme.exists():
        pytest.skip("no README yet")
    for line in readme.read_text(encoding="utf-8").splitlines():
        if re.search(r"41\s*(to|->|→)\s*3", line):
            assert "motivating example" in line.lower(), line


# ---- invalid input ---------------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "eval" / "fixtures" / "a").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "eval" / "cases.toml"
    path.write_text(text, encoding="utf-8")
    return path


REPO = '[[repo]]\nid = "a"\nsource = "local:eval/fixtures/a"\n'


def case(**over: str) -> str:
    fields = {
        "id": "c1",
        "repo": "a",
        "package": "pyyaml",
        "advisory": "GHSA-8q59-q68h-6hv4",
        "label": "uncertain",
        "rationale": "because",
    }
    fields.update(over)
    return "[[case]]\n" + "".join(f'{k} = "{v}"\n' for k, v in fields.items())


def test_ac33_loader_accepts_a_minimal_valid_file(tmp_path):
    suite = load_cases(write(tmp_path, REPO + case()))

    assert [c.id for c in suite.cases] == ["c1"]
    assert suite.local_path(suite.repos[0]) == tmp_path / "eval" / "fixtures" / "a"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (REPO + case(repo="nope"), "undefined repo"),
        (REPO + case(advisory="not an id"), "invalid OSV id"),
        (REPO + case(label="reachable"), "invalid table"),
        (REPO + case() + case(), "duplicate case id"),
        (REPO + REPO + case(), "duplicate repo id"),
        ('[[repo]]\nid = "a"\nsource = "local:eval/fixtures/missing"\n' + case(), "does not exist"),
        ('[[repo]]\nid = "g"\nsource = "github:o/r@main"\n', "invalid table"),
        ('[[repo]]\nid = "g"\nsource = "local:eval/fixtures/../x"\n', "invalid table"),
        ("[[repo]\n", "not valid TOML"),
    ],
)
def test_ac33_loader_rejects_invalid_files(tmp_path, text, message):
    with pytest.raises(CasesError, match=message):
        load_cases(write(tmp_path, text))


def test_ac33_loader_reports_a_missing_file(tmp_path):
    with pytest.raises(CasesError, match="cannot read"):
        load_cases(tmp_path / "absent.toml")
