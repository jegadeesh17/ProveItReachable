"""Evaluation metrics on hand-computed sets (AC-35) and the published figure rule (AC-39)."""

from datetime import date
from pathlib import Path

import pytest

from pir.evaluation.metrics import (
    LR,
    NO_PATH,
    UNCERTAIN,
    bar_met,
    compute,
    eval_figure,
    load_eval_figure,
)
from pir.models.eval import EvalRunSummary

# (label, predicted). Hand count: TP 2, FP 2 (one no-path case and one uncertain case predicted LR).
PAIRS = [
    (LR, LR),
    (LR, LR),
    (LR, NO_PATH),
    (LR, UNCERTAIN),
    (NO_PATH, LR),
    (NO_PATH, NO_PATH),
    (NO_PATH, UNCERTAIN),
    (UNCERTAIN, UNCERTAIN),
    (UNCERTAIN, LR),
    (UNCERTAIN, NO_PATH),
]


def test_ac35_exact_values_on_a_hand_computed_set():
    m = compute(PAIRS)

    assert m.n == 10
    assert (m.true_positives, m.false_positives) == (2, 2)
    assert m.precision == 0.5  # 2 / (2 + 2)
    assert m.recall == 0.5  # 2 of the 4 cases labelled LR
    assert m.abstention_rate == 0.3  # 3 predicted uncertain of 10
    assert m.correct_abstentions == 1
    assert m.no_path_precision == pytest.approx(1 / 3)  # 3 predicted no-path, 1 right
    assert m.dangerous_misses == 1  # labelled LR, predicted no-path
    assert m.confusion[LR] == {LR: 2, NO_PATH: 1, UNCERTAIN: 1}
    assert m.confusion[NO_PATH] == {LR: 1, NO_PATH: 1, UNCERTAIN: 1}
    assert m.confusion[UNCERTAIN] == {LR: 1, NO_PATH: 1, UNCERTAIN: 1}
    assert m.errored == []


def test_ac35_precision_is_none_when_nothing_is_predicted_lr():
    m = compute([(LR, NO_PATH), (NO_PATH, NO_PATH), (UNCERTAIN, UNCERTAIN)])

    assert m.precision is None
    assert m.recall == 0.0
    assert not bar_met(m.precision)


def test_ac35_a_missing_prediction_is_scored_as_uncertain_and_errors_are_listed():
    m = compute([(LR, None), (UNCERTAIN, None)], errored=["b", "a", "a"])

    assert m.confusion[LR][UNCERTAIN] == 1
    assert m.correct_abstentions == 1
    assert m.abstention_rate == 1.0
    assert m.errored == ["a", "b"]


def test_ac35_empty_input_has_no_rates():
    m = compute([])

    assert (m.n, m.precision, m.recall, m.no_path_precision) == (0, None, None, None)
    assert m.abstention_rate == 0.0


def test_ac35_recall_is_none_when_no_case_is_labelled_lr():
    m = compute([(NO_PATH, LR)])

    assert m.recall is None
    assert m.precision == 0.0


def test_ac35_an_unknown_verdict_is_rejected():
    with pytest.raises(ValueError, match="not a verdict"):
        compute([("maybe", LR)])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="not a verdict"):
        compute([(LR, "reachable")])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("precision", "expected"),
    [(0.80, True), (0.79, False), (1.0, True), (0.0, False), (None, False)],
)
def test_ac36_bar_needs_precision_of_at_least_080(precision, expected):
    assert bar_met(precision) is expected


# ---- AC-39: the figure rule ------------------------------------------------------------------


def summary(**over) -> EvalRunSummary:
    fields = {
        "scored": True,
        "run_date": date(2026, 10, 27),
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "experiment": "eval-x",
        "n": 13,
        "errored": 0,
        "precision": 0.875,
        "recall": 0.6,
        "abstention_rate": 0.2,
        "bar": 0.8,
        "bar_met": True,
    }
    fields.update(over)
    return EvalRunSummary(**fields)


def test_ac39_figure_is_built_from_a_scored_summary():
    figure = eval_figure(summary())

    assert figure is not None
    assert (figure.precision, figure.n, figure.run_date) == (0.875, 13, date(2026, 10, 27))
    assert figure.model == "nvidia/nemotron-3-super-120b-a12b"


@pytest.mark.parametrize("over", [{"scored": False}, {"precision": None, "bar_met": False}])
def test_ac39_no_figure_for_an_unscored_run_or_a_null_precision(over):
    assert eval_figure(summary(**over)) is None


def test_ac39_load_figure_needs_a_valid_file(tmp_path: Path):
    good = tmp_path / "latest.json"
    good.write_text(summary().model_dump_json(), encoding="utf-8")
    assert load_eval_figure(good) is not None

    assert load_eval_figure(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"scored": true}', encoding="utf-8")
    assert load_eval_figure(bad) is None
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json", encoding="utf-8")
    assert load_eval_figure(garbage) is None
    unscored = tmp_path / "unscored.json"
    unscored.write_text(summary(scored=False).model_dump_json(), encoding="utf-8")
    assert load_eval_figure(unscored) is None


def test_ac39_the_committed_latest_json_follows_the_rule():
    path = Path(__file__).resolve().parents[2] / "eval" / "results" / "latest.json"
    published = EvalRunSummary.model_validate_json(path.read_text(encoding="utf-8"))

    figure = load_eval_figure(path)
    assert (figure is not None) == (published.scored and published.precision is not None)
