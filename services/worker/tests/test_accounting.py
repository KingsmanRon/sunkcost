"""G2: savings accounting follows the §7.4 table row by row."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sunkcost_worker.calibration import AccountedRun, account, cp_interval

BUNDLE = "b1"
MEAN_SAVING = {BUNDLE: Decimal("0.500000000")}


def _one(run: AccountedRun) -> tuple[Decimal, Decimal, int, int]:
    r = account([run], MEAN_SAVING)
    return r.verified_saving_usd, r.estimated_saving_usd, r.failures_caught, r.successes_lost


@pytest.mark.parametrize(("mode", "holdout"), [("shadow", False), ("enforce", True)])
def test_would_abort_failure_adds_observed_remaining_spend(mode: str, holdout: bool) -> None:
    run = AccountedRun(mode, holdout, "failure", Decimal("1.25"), 3, Decimal("0.40"))  # type: ignore[arg-type]
    assert _one(run) == (Decimal("0.85"), Decimal(0), 1, 0)


@pytest.mark.parametrize(("mode", "holdout"), [("shadow", False), ("enforce", True)])
def test_would_abort_success_is_a_success_lost(mode: str, holdout: bool) -> None:
    run = AccountedRun(mode, holdout, "success", Decimal("1.25"), 3, Decimal("0.40"))  # type: ignore[arg-type]
    assert _one(run) == (Decimal(0), Decimal(0), 0, 1)


@pytest.mark.parametrize("outcome", ["success", "failure", "abandoned"])
def test_no_would_abort_contributes_nothing(outcome: str) -> None:
    run = AccountedRun("shadow", False, outcome, Decimal("1.25"))  # type: ignore[arg-type]
    assert _one(run) == (Decimal(0), Decimal(0), 0, 0)


@pytest.mark.parametrize("outcome", ["success", "failure", None])
def test_enforced_abort_adds_the_bundles_estimate_whatever_the_outcome(outcome: str | None) -> None:
    run = AccountedRun("enforce", False, outcome, Decimal("0.30"), 3, Decimal("0.30"), 3, BUNDLE)  # type: ignore[arg-type]
    assert _one(run) == (Decimal(0), Decimal("0.500000000"), 0, 0)


def test_enforce_run_that_was_not_aborted_is_not_counted() -> None:
    # Not a holdout, so its outcome is selected by the policy and says nothing about savings.
    run = AccountedRun("enforce", False, "failure", Decimal("1.25"))
    assert _one(run) == (Decimal(0), Decimal(0), 0, 0)


def test_abandoned_would_abort_is_neither_saving_nor_loss() -> None:
    run = AccountedRun("shadow", False, "abandoned", Decimal("1.25"), 3, Decimal("0.40"))
    assert _one(run) == (Decimal(0), Decimal(0), 0, 0)


def test_report_totals_and_false_abort_interval() -> None:
    runs = (
        [AccountedRun("shadow", False, "failure", Decimal("2"), 1, Decimal("0.5"))] * 3
        + [AccountedRun("shadow", False, "success", Decimal("1"), 2, Decimal("0.5"))] * 2
        + [AccountedRun("shadow", False, "success", Decimal("1"))] * 38
        + [AccountedRun("shadow", False, None, Decimal("1"))] * 4
        + [AccountedRun("enforce", False, "failure", Decimal("1"), 1, Decimal("0.1"), 1, BUNDLE)]
        * 2
    )
    r = account(runs, MEAN_SAVING)
    assert r.runs == 49
    assert r.verified_saving_usd == Decimal("4.5")
    assert r.estimated_saving_usd == Decimal("1.000000000")
    assert (r.failures_caught, r.successes_lost, r.observed_successes) == (3, 2, 40)
    assert r.outcome_pending == 4
    assert r.enforced_aborts == 2
    assert r.false_abort_rate == pytest.approx(2 / 40)
    assert (r.fa_ci_low, r.fa_ci_high) == cp_interval(2, 40)
