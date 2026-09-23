"""Test-split evaluation (ARCHITECTURE.md §7.3) and the savings/recall trade-off curve."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from sklearn.metrics import roc_auc_score

from sunkcost.core import LogisticModel
from sunkcost_worker.calibration.data import RunData
from sunkcost_worker.calibration.stats import cp_interval


@dataclass(frozen=True)
class RunEvaluation:
    run_id: str
    failed: bool
    abort_step: int | None
    """First decision point k with score >= τ: the run would stop after k steps."""
    max_score: float | None
    """Highest score over all decision points (None when the run has none)."""
    total_cost: Decimal
    cost_through_abort: Decimal | None
    decision_steps: tuple[int, ...]
    running_max: tuple[float, ...]
    cost_through: tuple[Decimal, ...]
    """cost_through[j] is the cost of steps 1..decision_steps[j]."""

    @property
    def saved(self) -> Decimal:
        """Spend after the abort point; counted as a saving only for failures."""
        if self.cost_through_abort is None:
            return Decimal(0)
        return self.total_cost - self.cost_through_abort


def evaluate_run(
    run: RunData, model: LogisticModel, threshold: float, min_steps: int
) -> RunEvaluation:
    vectors = run.feature_vectors()
    steps = tuple(run.decision_points(min_steps))
    abort: int | None = None
    running: list[float] = []
    best = float("-inf")
    for k in steps:
        score = model.score(vectors[k])
        best = max(best, score)
        running.append(best)
        if abort is None and score >= threshold:
            abort = k
    cumulative: list[Decimal] = []
    total = Decimal(0)
    for c in run.costs:
        total += c
        cumulative.append(total)
    cost_through = tuple(cumulative[k - 1] if k > 0 else Decimal(0) for k in steps)
    return RunEvaluation(
        run_id=run.run_id,
        failed=run.outcome == "failure",
        abort_step=abort,
        max_score=running[-1] if running else None,
        total_cost=total,
        cost_through_abort=None
        if abort is None
        else (cumulative[abort - 1] if abort > 0 else Decimal(0)),
        decision_steps=steps,
        running_max=tuple(running),
        cost_through=cost_through,
    )


@dataclass(frozen=True)
class TestMetrics:
    n_success: int
    n_failure: int
    false_aborts: int
    false_abort_rate: float
    false_abort_ci: tuple[float, float]
    failures_caught: int
    saved: Decimal
    total_cost: Decimal
    failed_cost: Decimal
    savings_share: float
    """Saved spend on aborted failures ÷ total spend of all test runs (§7.3)."""
    failed_spend_saved_share: float
    """Saved spend on aborted failures ÷ total spend of failed test runs."""
    auroc: float | None
    mean_saved_per_caught_failure: Decimal | None


def _ratio(num: Decimal, den: Decimal) -> float:
    return float(num / den) if den > 0 else 0.0


def summarize(evaluations: Sequence[RunEvaluation], confidence: float = 0.95) -> TestMetrics:
    successes = [e for e in evaluations if not e.failed]
    failures = [e for e in evaluations if e.failed]
    false_aborts = sum(1 for e in successes if e.abort_step is not None)
    caught = [e for e in failures if e.abort_step is not None]
    saved = sum((e.saved for e in caught), Decimal(0))
    total = sum((e.total_cost for e in evaluations), Decimal(0))
    failed_cost = sum((e.total_cost for e in failures), Decimal(0))
    auroc: float | None = None
    if successes and failures:
        labels = np.array([1 if e.failed else 0 for e in evaluations])
        # Runs without decision points rank below every sigmoid output.
        scores = np.array([-1.0 if e.max_score is None else e.max_score for e in evaluations])
        auroc = float(roc_auc_score(labels, scores))
    return TestMetrics(
        n_success=len(successes),
        n_failure=len(failures),
        false_aborts=false_aborts,
        false_abort_rate=false_aborts / len(successes) if successes else 0.0,
        false_abort_ci=cp_interval(false_aborts, len(successes), confidence),
        failures_caught=len(caught),
        saved=saved,
        total_cost=total,
        failed_cost=failed_cost,
        savings_share=_ratio(saved, total),
        failed_spend_saved_share=_ratio(saved, failed_cost),
        auroc=auroc,
        mean_saved_per_caught_failure=(saved / len(caught)) if caught else None,
    )


def evaluate_test(
    runs: Sequence[RunData], model: LogisticModel, threshold: float, min_steps: int
) -> tuple[TestMetrics, list[RunEvaluation]]:
    evaluations = [evaluate_run(r, model, threshold, min_steps) for r in runs]
    return summarize(evaluations), evaluations


@dataclass(frozen=True)
class CurvePoint:
    threshold: float
    successes_lost: float
    """Share of test successes that would have been aborted."""
    failed_spend_saved: float
    """Share of failed-run spend that would have been saved."""
    savings_share: float
    """Share of all test spend saved on failures."""


def tradeoff_curve(
    evaluations: Sequence[RunEvaluation], thresholds: Sequence[float]
) -> list[CurvePoint]:
    """Savings against successes lost for many thresholds, from one scoring pass."""
    successes = [e for e in evaluations if not e.failed]
    failures = [e for e in evaluations if e.failed]
    taus = np.asarray(thresholds, dtype=np.float64)
    lost = np.zeros(len(taus))
    for e in successes:
        if e.max_score is not None:
            lost += e.max_score >= taus
    saved = np.zeros(len(taus))
    for e in failures:
        if not e.running_max:
            continue
        # The first decision point whose running max reaches τ is where the run stops.
        idx = np.searchsorted(np.asarray(e.running_max), taus, side="left")
        hit = idx < len(e.running_max)
        through = np.array([float(e.cost_through[i]) for i in idx[hit]])
        saved[hit] += float(e.total_cost) - through
    total = float(sum((e.total_cost for e in evaluations), Decimal(0)))
    failed_total = float(sum((e.total_cost for e in failures), Decimal(0)))
    n_success = max(len(successes), 1)
    return [
        CurvePoint(
            threshold=float(t),
            successes_lost=float(lost[i]) / n_success,
            failed_spend_saved=float(saved[i]) / failed_total if failed_total > 0 else 0.0,
            savings_share=float(saved[i]) / total if total > 0 else 0.0,
        )
        for i, t in enumerate(taus)
    ]
