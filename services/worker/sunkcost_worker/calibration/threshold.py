"""Threshold selection with a recall guarantee (ARCHITECTURE.md §7.3).

For each calibration success, m_i is the highest score it reached at any decision point. A threshold
τ falsely aborts c(τ) = #{i : m_i >= τ} of the n successes. Candidates (the m_i plus 1.0) are
scanned from 1.0 downward; the scan stops at the first candidate whose one-sided Clopper–Pearson
upper bound at confidence 1 - δ exceeds α. c(τ) is monotone in τ, so this is fixed-sequence testing
and the scan does not weaken the bound.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from sunkcost.core import LogisticModel
from sunkcost_worker.calibration.data import RunData
from sunkcost_worker.calibration.stats import cp_upper


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float | None
    """The smallest feasible candidate, or None when even τ = 1.0 fails."""
    false_aborts: int
    """c(τ) at the chosen threshold (at τ = 1.0 when infeasible)."""
    n: int
    ucb: float
    candidates_tested: int


def max_decision_score(run: RunData, model: LogisticModel, min_steps: int) -> float | None:
    """The highest score over the run's decision points; None if it has none."""
    vectors = run.feature_vectors()
    best: float | None = None
    for k in run.decision_points(min_steps):
        score = model.score(vectors[k])
        if best is None or score > best:
            best = score
    return best


def select_threshold(
    max_scores: Sequence[float | None], alpha: float, delta: float
) -> ThresholdResult:
    """Scan candidates from 1.0 downward; keep the last one whose UCB(c(τ)) <= α."""
    if not 0.0 < alpha < 1.0 or not 0.0 < delta < 1.0:
        raise ValueError("alpha and delta must be in (0, 1)")
    n = len(max_scores)
    ascending = sorted(m for m in max_scores if m is not None)
    candidates = sorted({1.0, *(m for m in ascending if m <= 1.0)}, reverse=True)
    best: ThresholdResult | None = None
    first_failure: ThresholdResult | None = None
    for tested, tau in enumerate(candidates, start=1):
        c = len(ascending) - bisect.bisect_left(ascending, tau)  # #{m_i >= tau}
        ucb = cp_upper(c, n, 1.0 - delta) if n else 1.0
        result = ThresholdResult(tau, c, n, ucb, tested)
        if ucb > alpha:
            first_failure = result
            break
        best = result
    if best is not None:
        return best
    if first_failure is None:  # pragma: no cover - the candidates always include 1.0
        raise AssertionError("no threshold candidates")
    return ThresholdResult(None, first_failure.false_aborts, n, first_failure.ucb, 1)
