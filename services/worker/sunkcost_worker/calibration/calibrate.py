"""The calibration job as a library call: data → split → train → threshold → test → gate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from sunkcost_worker.calibration.data import RunData, labelled_uncensored
from sunkcost_worker.calibration.evaluate import RunEvaluation, TestMetrics, evaluate_test
from sunkcost_worker.calibration.threshold import (
    ThresholdResult,
    max_decision_score,
    select_threshold,
)
from sunkcost_worker.calibration.train import DEFAULT_C_GRID, TrainedModel, TrainingError, fit

Status = Literal["validated", "rejected", "insufficient_data"]


@dataclass(frozen=True)
class CalibrationSettings:
    target_recall: str = "0.95"
    delta: str = "0.05"
    min_steps: int = 3
    min_savings_share: str = "0.02"
    min_auroc: float = 0.6
    min_successes: int = 100
    """Minimum successes in each of the calibration and test splits."""
    min_failures: int = 30
    """Minimum failures in each of the calibration and test splits."""
    c_grid: tuple[float, ...] = DEFAULT_C_GRID
    cv_folds: int = 5

    @property
    def alpha(self) -> float:
        """α = 1 − target_recall, computed exactly and then rounded to float64."""
        return float(Decimal(1) - Decimal(self.target_recall))


@dataclass(frozen=True)
class Split:
    train: list[RunData]
    calibration: list[RunData]
    test: list[RunData]


def time_split(runs: Sequence[RunData]) -> Split:
    """Oldest 60% train, next 20% calibration, newest 20% test, ordered by start time."""
    ordered = sorted(runs, key=lambda r: (r.started_at_us, r.run_id))
    n = len(ordered)
    i1, i2 = (3 * n) // 5, (4 * n) // 5
    return Split(ordered[:i1], ordered[i1:i2], ordered[i2:])


def _counts(split: Split) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, runs in (
        ("train", split.train),
        ("calibration", split.calibration),
        ("test", split.test),
    ):
        out[f"{name}_success"] = sum(1 for r in runs if r.outcome == "success")
        out[f"{name}_failure"] = sum(1 for r in runs if r.outcome == "failure")
    return out


@dataclass(frozen=True)
class CalibrationResult:
    status: Status
    reason: str | None
    counts: dict[str, int]
    trained: TrainedModel | None = None
    threshold: ThresholdResult | None = None
    test: TestMetrics | None = None
    test_evaluations: list[RunEvaluation] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    metrics: dict[str, int | str] = field(default_factory=dict)

    @property
    def bundle_model(self) -> dict[str, Any] | None:
        return None if self.trained is None else self.trained.bundle_model


def _f(x: float) -> str:
    return repr(float(x))


def calibrate(
    runs: Sequence[RunData], settings: CalibrationSettings | None = None
) -> CalibrationResult:
    s = settings or CalibrationSettings()
    alpha, delta = s.alpha, float(s.delta)
    split = time_split(labelled_uncensored(runs))
    counts = _counts(split)
    short = [
        f"{part} has {counts[f'{part}_{kind}']} {plural}"
        for part in ("calibration", "test")
        for kind, plural, minimum in (
            ("success", "successes", s.min_successes),
            ("failure", "failures", s.min_failures),
        )
        if counts[f"{part}_{kind}"] < minimum
    ]
    if short:
        need = f"need >= {s.min_successes} successes and >= {s.min_failures} failures in each"
        return CalibrationResult("insufficient_data", f"{'; '.join(short)} ({need})", counts)

    try:
        trained = fit(split.train, s.min_steps, c_grid=s.c_grid, folds=s.cv_folds)
    except TrainingError as exc:
        return CalibrationResult("rejected", f"cannot_train: {exc}", counts)

    success_max = [
        max_decision_score(r, trained.model, s.min_steps)
        for r in split.calibration
        if r.outcome == "success"
    ]
    chosen = select_threshold(success_max, alpha, delta)
    base_metrics: dict[str, int | str] = {
        "n_train": len(split.train),
        "n_train_rows": trained.n_rows,
        "n_calib_success": counts["calibration_success"],
        "n_calib_failure": counts["calibration_failure"],
        "n_test_success": counts["test_success"],
        "n_test_failure": counts["test_failure"],
        "calib_false_aborts": chosen.false_aborts,
        "calib_ucb": _f(chosen.ucb),
        "l2_c": _f(trained.c),
    }
    if chosen.threshold is None:
        return CalibrationResult(
            "rejected", "cannot_meet_recall", counts, trained, chosen, metrics=base_metrics
        )

    test, evaluations = evaluate_test(split.test, trained.model, chosen.threshold, s.min_steps)
    metrics = dict(base_metrics)
    metrics.update(
        {
            "test_false_abort_rate": _f(test.false_abort_rate),
            "test_fa_ci_low": _f(test.false_abort_ci[0]),
            "test_fa_ci_high": _f(test.false_abort_ci[1]),
            "test_savings_share": _f(test.savings_share),
            "test_failed_spend_saved_share": _f(test.failed_spend_saved_share),
            "auroc": _f(test.auroc if test.auroc is not None else 0.0),
            "test_failures_caught": test.failures_caught,
        }
    )
    if test.mean_saved_per_caught_failure is not None:
        metrics["test_mean_saved_cost_usd"] = format(
            test.mean_saved_per_caught_failure.quantize(Decimal("1e-9")), "f"
        )
    decision = {
        "threshold": _f(chosen.threshold),
        "min_steps": s.min_steps,
        "target_recall": s.target_recall,
        "delta": s.delta,
    }

    failed_gates = []
    if test.false_abort_rate > alpha:
        failed_gates.append("test_false_abort_rate_above_alpha")
    if test.savings_share < float(s.min_savings_share):
        failed_gates.append("savings_share_below_minimum")
    if test.auroc is None or test.auroc < s.min_auroc:
        failed_gates.append("auroc_below_minimum")
    status: Status = "rejected" if failed_gates else "validated"
    return CalibrationResult(
        status,
        ",".join(failed_gates) or None,
        counts,
        trained,
        chosen,
        test,
        evaluations,
        decision,
        metrics,
    )
