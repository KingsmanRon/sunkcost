"""G2: Clopper–Pearson, threshold selection, training rows, evaluation and the calibrate job."""

from __future__ import annotations

import random
from decimal import Decimal
from itertools import pairwise

import pytest
from scipy.stats import binomtest

from sunkcost.core import StepRecord
from sunkcost_spec import Bundle
from sunkcost_worker.calibration import (
    CalibrationSettings,
    RunData,
    calibrate,
    cp_interval,
    cp_lower,
    cp_upper,
    evaluate_run,
    select_threshold,
    summarize,
    time_split,
    tradeoff_curve,
    training_rows,
)
from sunkcost_worker.synthetic import WorkloadProcess, generate_history

# --- Clopper–Pearson ------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 5, 10, 37, 100, 402, 1000])
@pytest.mark.parametrize("frac", [0.0, 0.01, 0.1, 0.33, 0.5, 0.9, 1.0])
def test_interval_matches_scipy_exact(n: int, frac: float) -> None:
    k = round(frac * n)
    ref = binomtest(k, n).proportion_ci(confidence_level=0.95, method="exact")
    low, high = cp_interval(k, n, 0.95)
    assert low == pytest.approx(ref.low, abs=1e-12)
    assert high == pytest.approx(ref.high, abs=1e-12)


@pytest.mark.parametrize("n", [1, 10, 100, 250, 1000])
@pytest.mark.parametrize("delta", [0.01, 0.05, 0.1])
def test_one_sided_closed_forms(n: int, delta: float) -> None:
    # k = 0: P(X <= 0 | p) = (1 - p)^n = delta  =>  U = 1 - delta^(1/n); symmetric for k = n.
    assert cp_upper(0, n, 1 - delta) == pytest.approx(1 - delta ** (1 / n), rel=1e-12)
    assert cp_lower(n, n, 1 - delta) == pytest.approx(delta ** (1 / n), rel=1e-12)
    assert cp_upper(n, n, 1 - delta) == 1.0
    assert cp_lower(0, n, 1 - delta) == 0.0


def test_textbook_values() -> None:
    # Exact 95% intervals, e.g. Brown, Cai & DasGupta (2001) and standard tables.
    assert cp_interval(3, 10) == pytest.approx((0.066739, 0.652453), abs=1e-6)
    assert cp_interval(0, 10) == pytest.approx((0.0, 0.308497), abs=1e-6)


def test_cp_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="k <= n"):
        cp_upper(5, 4, 0.95)
    with pytest.raises(ValueError, match="confidence"):
        cp_upper(1, 4, 1.0)


# --- threshold selection --------------------------------------------------------------------


def _brute_force(max_scores: list[float | None], alpha: float, delta: float) -> float | None:
    """Fixed-sequence testing written out directly, for comparison."""
    n = len(max_scores)
    finite = [m for m in max_scores if m is not None]
    chosen = None
    for tau in sorted({1.0, *finite}, reverse=True):
        c = sum(1 for m in finite if m >= tau)
        if cp_upper(c, n, 1 - delta) > alpha:
            break
        chosen = tau
    return chosen


def test_threshold_matches_brute_force_on_random_inputs() -> None:
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(1, 400)
        scores: list[float | None] = [
            None if rng.random() < 0.05 else round(rng.random(), rng.choice([2, 3, 17]))
            for _ in range(n)
        ]
        alpha, delta = rng.choice([0.05, 0.1, 0.2]), rng.choice([0.05, 0.1])
        assert select_threshold(scores, alpha, delta).threshold == _brute_force(
            scores, alpha, delta
        )


def test_all_equal_scores_fall_back_to_never_abort() -> None:
    result = select_threshold([0.5] * 150, alpha=0.05, delta=0.05)
    assert result.threshold == 1.0
    assert result.false_aborts == 0


def test_no_feasible_threshold() -> None:
    # 50 successes cannot certify a 5% false-abort rate at 95% confidence: 1 - 0.05^(1/50) > 0.05.
    result = select_threshold([0.1] * 50, alpha=0.05, delta=0.05)
    assert result.threshold is None
    assert result.ucb == pytest.approx(1 - 0.05 ** (1 / 50))


def test_threshold_allows_exactly_the_certifiable_number_of_false_aborts() -> None:
    scores: list[float | None] = [i / 1000 for i in range(1000)]  # distinct, top value 0.999
    result = select_threshold(scores, alpha=0.05, delta=0.05)
    assert result.threshold is not None
    c = result.false_aborts
    assert cp_upper(c, 1000, 0.95) <= 0.05 < cp_upper(c + 1, 1000, 0.95)
    assert result.threshold == scores[1000 - c]


def test_runs_without_decision_points_count_but_never_abort() -> None:
    scores: list[float | None] = [None] * 200 + [0.9]
    result = select_threshold(scores, alpha=0.05, delta=0.05)
    assert result.n == 201
    assert result.threshold == 0.9
    assert result.false_aborts == 1


# --- data handling --------------------------------------------------------------------------


def _run(
    run_id: str, outcome: str, costs: list[str], start: int = 0, scores_hint: int = 0
) -> RunData:
    steps = tuple(
        StepRecord("tool", None, f"t{scores_hint}", None, 0, 0, float(c), False, start + i)
        for i, c in enumerate(costs)
    )
    return RunData(run_id, start, steps, tuple(Decimal(c) for c in costs), outcome)  # type: ignore[arg-type]


def test_time_split_orders_by_start_time() -> None:
    runs = [_run(f"r{i:03d}", "success", ["1"], start=1000 - i) for i in range(10)]
    split = time_split(runs)
    assert (len(split.train), len(split.calibration), len(split.test)) == (6, 2, 2)
    starts = [r.started_at_us for r in split.train + split.calibration + split.test]
    assert starts == sorted(starts)


def test_decision_points_exclude_the_final_prefix() -> None:
    run = _run("r", "success", ["1"] * 5)
    assert list(run.decision_points(3)) == [3, 4]
    assert list(run.decision_points(5)) == []


def test_training_rows_give_each_run_weight_one() -> None:
    runs = [
        _run("a", "success", ["1"] * 5),
        _run("b", "failure", ["1"] * 12),
        _run("c", "failure", ["1"] * 3),
    ]
    rows = training_rows(runs, min_steps=2)
    assert rows.x.shape == (3 + 10 + 1, 12)
    for group in set(rows.group.tolist()):
        assert rows.weight[rows.group == group].sum() == pytest.approx(1.0)
    assert rows.y.tolist() == [0] * 3 + [1] * 11


# --- evaluation and savings -----------------------------------------------------------------


class _StepScore:
    """A model whose score is the step index / 10, to make abort points predictable."""

    def score(self, x: list[float]) -> float:
        return x[0] / 10.0


def test_evaluate_run_abort_point_and_savings() -> None:
    run = _run("f", "failure", ["0.10", "0.20", "0.30", "0.40", "0.50", "0.60"])
    ev = evaluate_run(run, _StepScore(), threshold=0.35, min_steps=2)  # type: ignore[arg-type]
    # Decision points k = 2..5 score 0.2..0.5; the first >= 0.35 is k = 4.
    assert ev.abort_step == 4
    assert ev.cost_through_abort == Decimal("1.00")
    assert ev.saved == Decimal("1.10")
    assert ev.max_score == pytest.approx(0.5)


def test_summary_and_curve_agree() -> None:
    runs = [
        _run("f1", "failure", ["1"] * 8),
        _run("f2", "failure", ["2"] * 3),
        _run("s1", "success", ["1"] * 5),
        _run("s2", "success", ["1"] * 2),
    ]
    evs = [evaluate_run(r, _StepScore(), 0.4, 1) for r in runs]  # type: ignore[arg-type]
    m = summarize(evs)
    # f1 aborts at k=4 (saves 4 of 8); f2 peaks at 0.2; s1 peaks at 0.4 -> false abort; s2 at 0.1.
    assert (m.false_aborts, m.failures_caught, m.saved) == (1, 1, Decimal(4))
    assert m.savings_share == pytest.approx(4 / (8 + 6 + 5 + 2))
    assert m.failed_spend_saved_share == pytest.approx(4 / 14)
    point = tradeoff_curve(evs, [0.4])[0]
    assert point.successes_lost == pytest.approx(0.5)
    assert point.failed_spend_saved == pytest.approx(m.failed_spend_saved_share)
    assert point.savings_share == pytest.approx(m.savings_share)


# --- the calibrate job end to end on synthetic data -----------------------------------------


@pytest.fixture(scope="module")
def history() -> list[RunData]:
    return generate_history(random.Random(42), WorkloadProcess(p_failure=0.35, signal=0.9), 1500)


def test_calibrate_produces_a_validated_bundle(history: list[RunData]) -> None:
    result = calibrate(history, CalibrationSettings())
    assert result.status == "validated", result.reason
    assert result.test is not None
    assert result.test.false_abort_rate <= 0.05
    assert result.test.savings_share >= 0.02
    assert result.test.auroc is not None
    assert result.test.auroc >= 0.6
    assert result.decision is not None
    assert result.bundle_model is not None
    # The pieces slot into a schema-valid bundle.
    bundle = {
        "schema_version": "1",
        "bundle_id": "01a03336-78b8-7c55-b624-8e5a0fac9d43",
        "org_id": "01a03336-8088-7f77-9846-a07c2bc1bf65",
        "project_id": "01a03336-8470-7088-a957-b18d3cd2c076",
        "workload_id": "01a03336-7ca0-7e66-8735-9f6b1ab0ae54",
        "workload": "synthetic",
        "version": 1,
        "feature_spec": "features.v1",
        "mode": "shadow",
        "holdout_rate": "0.05",
        "model": result.bundle_model,
        "decision": result.decision,
        "metrics": result.metrics,
        "issued_at": "2026-09-23T00:00:00Z",
        "expires_at": "2026-09-30T00:00:00Z",
    }
    Bundle.model_validate(bundle)


def test_calibrate_is_deterministic(history: list[RunData]) -> None:
    a, b = calibrate(history), calibrate(history)
    assert a.bundle_model == b.bundle_model
    assert a.decision == b.decision
    assert a.metrics == b.metrics


def test_calibrate_reports_insufficient_data(history: list[RunData]) -> None:
    result = calibrate(history[:300])
    assert result.status == "insufficient_data"
    assert result.reason is not None
    assert "calibration has" in result.reason
    assert result.counts["calibration_success"] < 100


def test_calibrate_ignores_censored_and_unlabelled_runs(history: list[RunData]) -> None:
    from dataclasses import replace

    noisy = [replace(r, mode="enforce", aborted_step=1) for r in history[:200]]
    noisy += [replace(r, outcome=None) for r in history[200:300]]
    noisy += [replace(r, outcome="abandoned") for r in history[300:400]]
    result = calibrate(noisy + history[400:])
    counts = result.counts
    assert sum(counts.values()) == len(history) - 400


def test_rejected_when_recall_cannot_be_certified(history: list[RunData]) -> None:
    result = calibrate(history, CalibrationSettings(target_recall="0.999", delta="0.01"))
    assert result.status == "rejected"
    assert result.reason == "cannot_meet_recall"


def test_rejected_when_savings_are_too_small(history: list[RunData]) -> None:
    result = calibrate(history, CalibrationSettings(min_savings_share="0.99"))
    assert result.status == "rejected"
    assert result.reason is not None
    assert "savings_share_below_minimum" in result.reason


def test_threshold_is_monotone_in_target_recall(history: list[RunData]) -> None:
    taus = []
    for target in ("0.8", "0.9", "0.95", "0.99"):
        r = calibrate(history, CalibrationSettings(target_recall=target))
        assert r.threshold is not None
        taus.append(r.threshold.threshold if r.threshold.threshold is not None else 2.0)
    assert all(a <= b for a, b in pairwise(taus))
