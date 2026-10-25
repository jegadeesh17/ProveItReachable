"""The test-side schema validator (tests/fakes/schema_subset.py) must reject what it should.

The wiring tests rely on it to check reports against schemas/report.schema.json, so these tests
show that it can fail: each case is a document the published schema forbids.
"""

import pytest
from fakes.schema_subset import validate_document

SCHEMA = {
    "$defs": {
        "Verdict": {"enum": ["likely reachable", "uncertain"], "type": "string"},
    },
    "additionalProperties": False,
    "properties": {
        "count": {"minimum": 0, "type": "integer"},
        "when": {"anyOf": [{"format": "date-time", "type": "string"}, {"type": "null"}]},
        "verdict": {"$ref": "#/$defs/Verdict"},
        "tags": {"items": {"maxLength": 5, "type": "string"}, "minItems": 1, "type": "array"},
        "by_verdict": {
            "additionalProperties": {"minimum": 0, "type": "integer"},
            "propertyNames": {"$ref": "#/$defs/Verdict"},
            "type": "object",
        },
    },
    "required": ["count", "verdict"],
    "title": "Doc",
    "type": "object",
}
VALID = {
    "count": 1,
    "when": None,
    "verdict": "uncertain",
    "tags": ["ab"],
    "by_verdict": {"likely reachable": 2},
}


def test_a_valid_document_has_no_errors():
    assert validate_document(VALID, SCHEMA) == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"count": "1"}, "expected integer, got str"),  # no int-as-string coercion
        ({"count": True}, "expected integer, got bool"),  # a bool is not an integer here
        ({"count": -1}, "below the minimum"),
        ({"verdict": "Likely reachable"}, "not one of the allowed values"),
        ({"extra": 1}, "unexpected property 'extra'"),
        ({"when": "yesterday"}, "is not a date-time"),
        ({"tags": ["toolong"]}, "longer than 5"),
        ({"tags": []}, "fewer than 1 items"),
        ({"by_verdict": {"Likely reachable": 1}}, "not one of the allowed values"),
        ({"by_verdict": {"uncertain": -3}}, "below the minimum"),
    ],
)
def test_a_forbidden_document_is_reported(change, fragment):
    errors = validate_document({**VALID, **change}, SCHEMA)
    assert any(fragment in error for error in errors), errors


def test_a_missing_required_property_is_reported():
    doc = {k: v for k, v in VALID.items() if k != "verdict"}
    assert any("missing required property 'verdict'" in e for e in validate_document(doc, SCHEMA))


def test_an_unknown_keyword_raises_instead_of_passing():
    with pytest.raises(NotImplementedError, match="unsupported schema keywords"):
        validate_document(VALID, {"type": "object", "pattern": "^x"})
