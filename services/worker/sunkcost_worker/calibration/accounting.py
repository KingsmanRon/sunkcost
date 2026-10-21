"""Savings accounting (ARCHITECTURE.md §7.4).

- Shadow or holdout run with a would-abort step k:
  - failure: ``verified_saving += total_cost − cost_through_k``
  - success: ``successes_lost += 1``, reported with a confidence interval
- Shadow or holdout run without a would-abort step: no contribution.
- Enforce-mode run that was aborted: ``estimated_saving +=`` the bundle's mean remaining cost of
  aborted failures in its test split.

Verified and estimated savings are kept apart and never summed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from sunkcost_worker.calibration.data import Mode, Outcome
from sunkcost_worker.calibration.stats import cp_interval


@dataclass(frozen=True)
class AccountedRun:
    mode: Mode
    holdout: bool
    outcome: Outcome | None
    total_cost: Decimal
    would_abort_step: int | None = None
    cost_through_would_abort: Decimal | None = None
    aborted_step: int | None = None
    bundle_id: str | None = None


@dataclass(frozen=True)
class SavingsReport:
    runs: int
    verified_saving_usd: Decimal
    estimated_saving_usd: Decimal
    failures_caught: int
    successes_lost: int
    observed_successes: int
    """Successes among uncensored runs: the denominator of the false-abort rate."""
    false_abort_rate: float | None
    fa_ci_low: float | None
    fa_ci_high: float | None
    outcome_pending: int
    enforced_aborts: int


def account(
    runs: Iterable[AccountedRun],
    mean_saving_by_bundle: Mapping[str, Decimal],
    *,
    confidence: float = 0.95,
) -> SavingsReport:
    """Aggregate one period. ``mean_saving_by_bundle`` is each bundle's
    ``test_mean_saved_cost_usd``: the mean remaining cost of aborted failures in its test split."""
    total_runs = verified_count = lost = successes = pending = aborts = 0
    verified = Decimal(0)
    estimated = Decimal(0)
    for run in runs:
        total_runs += 1
        if run.aborted_step is not None:
            if run.mode == "enforce" and not run.holdout:
                aborts += 1
                if run.bundle_id is not None:
                    estimated += mean_saving_by_bundle.get(run.bundle_id, Decimal(0))
            continue  # an aborted run's counterfactual outcome is unknown
        if not (run.mode == "shadow" or run.holdout):
            continue
        if run.outcome is None:
            pending += 1
            continue
        if run.outcome == "success":
            successes += 1
        if run.would_abort_step is None or run.outcome == "abandoned":
            continue
        if run.outcome == "failure":
            if run.cost_through_would_abort is None:
                raise ValueError("a would-abort run needs cost_through_would_abort")
            verified += run.total_cost - run.cost_through_would_abort
            verified_count += 1
        else:
            lost += 1
    low = high = rate = None
    if successes:
        rate = lost / successes
        low, high = cp_interval(lost, successes, confidence)
    return SavingsReport(
        runs=total_runs,
        verified_saving_usd=verified,
        estimated_saving_usd=estimated,
        failures_caught=verified_count,
        successes_lost=lost,
        observed_successes=successes,
        false_abort_rate=rate,
        fa_ci_low=low,
        fa_ci_high=high,
        outcome_pending=pending,
        enforced_aborts=aborts,
    )
