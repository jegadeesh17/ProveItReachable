"""AC-32 (schema part): the committed report schema equals `python -m pir schema`."""

import json
from pathlib import Path

from pir.cli import main, report_schema_json

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "report.schema.json"


def committed() -> str:
    return SCHEMA_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_ac32_schema_command_output_equals_committed_file(capsys):
    assert main(["schema"]) == 0
    out = capsys.readouterr().out
    assert out == committed(), "schemas/report.schema.json drifted; regenerate it"


def test_ac32_report_schema_json_is_stable_and_valid_json():
    first = report_schema_json()
    assert first == report_schema_json()
    schema = json.loads(first)
    assert schema["title"] == "ScanReport"
    assert schema["additionalProperties"] is False
    assert "findings" in schema["properties"]


def test_ac18_schema_pins_the_three_verdicts():
    defs = json.loads(committed())["$defs"]
    verdicts = defs["Finding"]["properties"]["verdict"]["enum"]
    assert sorted(verdicts) == ["likely reachable", "no reachable path detected", "uncertain"]


def test_ac32_schema_rejects_extra_properties_everywhere():
    schema = json.loads(committed())
    models = [schema, *schema["$defs"].values()]
    assert all(m["additionalProperties"] is False for m in models if m.get("type") == "object")
