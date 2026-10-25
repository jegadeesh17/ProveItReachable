"""Evaluation metrics (ARCHITECTURE section 15, OQ-8, AC-35) and the published eval figure.

Positive class: `likely reachable` (LR). Everything here is pure except `load_eval_figure`,
which reads one small JSON file.
"""

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import ValidationError

from pir.models import Strict, Verdict
from pir.models.api import EvalFigure
from pir.models.eval import EvalRunSummary

logger = logging.getLogger("pir.evaluation.metrics")

LR: Verdict = "likely reachable"
NO_PATH: Verdict = "no reachable path detected"
UNCERTAIN: Verdict = "uncertain"
VERDICTS: tuple[Verdict, ...] = (LR, NO_PATH, UNCERTAIN)
PRECISION_BAR = 0.80


class EvalMetrics(Strict):
    """Counts and rates of one scored run. `confusion[label][predicted]` is a 3x3 count."""

    n: int
    true_positives: int
    false_positives: int
    precision: float | None  # TP / (TP + FP); None when nothing is predicted LR
    recall: float | None  # TP / count(label == LR); None when no case is labelled LR
    abstention_rate: float  # count(predicted == uncertain) / n
    correct_abstentions: int  # predicted == label == uncertain
    no_path_precision: float | None  # None when nothing is predicted no-path
    dangerous_misses: int  # label == LR and predicted == no-path
    confusion: dict[Verdict, dict[Verdict, int]]
    errored: list[str]  # ids of errored or finding-missing cases (scored as uncertain)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def compute(
    pairs: Sequence[tuple[Verdict, Verdict | None]], errored: Iterable[str] = ()
) -> EvalMetrics:
    """Metrics for `(label, predicted)` pairs; a predicted `None` is scored as `uncertain`.

    `errored` only lists case ids to report; their pairs are already in `pairs`.
    """
    confusion: dict[Verdict, dict[Verdict, int]] = {y: dict.fromkeys(VERDICTS, 0) for y in VERDICTS}
    for label, predicted in pairs:
        if label not in VERDICTS or (predicted is not None and predicted not in VERDICTS):
            raise ValueError(f"not a verdict: {label!r} / {predicted!r}")
        confusion[label][predicted if predicted is not None else UNCERTAIN] += 1
    n = len(pairs)
    tp = confusion[LR][LR]
    fp = confusion[NO_PATH][LR] + confusion[UNCERTAIN][LR]
    labelled_lr = sum(confusion[LR].values())
    predicted_np = sum(confusion[y][NO_PATH] for y in VERDICTS)
    predicted_uncertain = sum(confusion[y][UNCERTAIN] for y in VERDICTS)
    return EvalMetrics(
        n=n,
        true_positives=tp,
        false_positives=fp,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, labelled_lr),
        abstention_rate=predicted_uncertain / n if n else 0.0,
        correct_abstentions=confusion[UNCERTAIN][UNCERTAIN],
        no_path_precision=_ratio(confusion[NO_PATH][NO_PATH], predicted_np),
        dangerous_misses=confusion[LR][NO_PATH],
        confusion=confusion,
        errored=sorted(set(errored)),
    )


def bar_met(precision: float | None, bar: float = PRECISION_BAR) -> bool:
    """AC-36: precision at or above the bar, and at least one LR prediction (precision not None)."""
    return precision is not None and precision >= bar


def eval_figure(summary: EvalRunSummary) -> EvalFigure | None:
    """The figure `/api/config` publishes: only from a scored run with a precision (AC-39)."""
    if not summary.scored or summary.precision is None:
        return None
    return EvalFigure(
        precision=summary.precision, n=summary.n, run_date=summary.run_date, model=summary.model
    )


def load_eval_figure(path: str | Path) -> EvalFigure | None:
    """`eval_figure` of the summary in `path`; None if the file is missing or does not validate."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("eval summary unreadable: %s", type(exc).__name__)
        return None
    try:
        summary = EvalRunSummary.model_validate_json(text)
    except ValidationError:
        logger.warning("eval summary does not validate; no eval figure is published")
        return None
    return eval_figure(summary)
