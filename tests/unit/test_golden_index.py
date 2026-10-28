"""Golden index of the eval fixtures and the canary (ADR-0037, AC-80).

Each repo is indexed in-process and compared exactly to a snapshot in
`tests/fixtures/golden_index/`, so a change to the index (default-argument fields, source roots)
cannot silently alter how the eval fixtures and the canary are analysed.
"""

import json
from pathlib import Path

import pytest

from pir.sandbox_agent import index as agent_index

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "golden_index"
SHA = "b" * 40

# repo directory (relative to the project root) -> target modules; the snapshot is <name>.json
REPOS: dict[str, list[str]] = {
    "eval/fixtures/jinja2_direct": ["jinja2"],
    "eval/fixtures/jinja2_dynamic": ["jinja2"],
    "eval/fixtures/jinja2_escape_only": ["jinja2"],
    "eval/fixtures/requests_mixed": ["requests"],
    "eval/fixtures/urllib3_direct": ["urllib3"],
    "eval/fixtures/urllib3_unused": ["urllib3"],
    "eval/fixtures/wheel_no_function": ["wheel"],
    "eval/fixtures/yaml_call_chain": ["yaml"],
    "eval/fixtures/yaml_class_method": ["yaml"],
    "eval/fixtures/yaml_conditional_import": ["yaml"],
    "eval/fixtures/yaml_dead_code": ["yaml"],
    "eval/fixtures/yaml_decorator_wrapped": ["yaml"],
    "eval/fixtures/yaml_direct": ["yaml"],
    "eval/fixtures/yaml_dynamic": ["yaml"],
    "eval/fixtures/yaml_dynamic_import": ["yaml"],
    "eval/fixtures/yaml_from_import_as": ["yaml"],
    "eval/fixtures/yaml_import_as": ["yaml"],
    "eval/fixtures/yaml_import_unused": ["yaml"],
    "eval/fixtures/yaml_init_reexport": ["yaml"],
    "eval/fixtures/yaml_multi_hop": ["yaml"],
    "eval/fixtures/yaml_not_imported": ["yaml"],
    "eval/fixtures/yaml_string_only": ["yaml"],
    "eval/fixtures/yaml_test_only": ["yaml"],
    "tests/fixtures/repos/canary": ["yaml"],
}


def _snapshot_name(rel: str) -> str:
    return rel.rsplit("/", 1)[-1] + ".json"


def test_every_eval_fixture_and_the_canary_has_a_snapshot():  # AC-80
    on_disk = {p.name for p in (ROOT / "eval" / "fixtures").iterdir() if p.is_dir()}
    covered = {r.rsplit("/", 1)[-1] for r in REPOS if r.startswith("eval/fixtures/")}
    assert on_disk == covered
    assert {p.name for p in GOLDEN.glob("*.json")} == {_snapshot_name(r) for r in REPOS}


@pytest.mark.parametrize("rel", sorted(REPOS))
def test_index_matches_the_golden_snapshot(rel):  # AC-80
    built = agent_index.build_index(str(ROOT / rel), SHA, REPOS[rel])
    expected = json.loads((GOLDEN / _snapshot_name(rel)).read_text(encoding="utf-8"))
    assert built == expected
