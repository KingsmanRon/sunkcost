"""Run the exact worker calibration on one dataset and summarise the held-out test split."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sunkcost_bench.common import CompactRun, batch_normalized_order
from sunkcost_bench.datasets import Dataset
from sunkcost_worker.calibration import (
    CalibrationResult,
    CalibrationSettings,
    calibrate,
    time_split,
    tradeoff_curve,
)

TARGETS = ("0.90", "0.95")
# The workload defaults (ARCHITECTURE.md §6.2, §7.3); no per-dataset tuning.
SETTINGS: dict[str, Any] = {"delta": "0.05", "min_steps": 3, "min_savings_share": "0.02"}
PRIMARY = "batch-normalized"
SHUFFLED = "shuffled"  # post-hoc sensitivity protocol (ADR-0003), not used for S1
SECONDARY = "absolute"
SHUFFLE_SEED = "sunkcost-bench-v1"
CURVE_POINTS = 240


def _target_summary(result: CalibrationResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": result.status,
        "reason": result.reason,
        "counts": result.counts,
    }
    if result.threshold is not None:
        out["calibration"] = {
            "threshold": result.threshold.threshold,
            "successes": result.threshold.n,
            "false_aborts": result.threshold.false_aborts,
            "ucb": result.threshold.ucb,
        }
    if result.trained is not None:
        out["training"] = {
            "runs": result.trained.n_runs,
            "rows": result.trained.n_rows,
            "l2_c": result.trained.c,
        }
    if result.test is not None:
        t = result.test
        out["test"] = {
            "successes": t.n_success,
            "failures": t.n_failure,
            "successes_lost": t.false_aborts,
            "successes_lost_share": t.false_abort_rate,
            "successes_lost_ci95": list(t.false_abort_ci),
            "successes_kept_share": 1.0 - t.false_abort_rate,
            "failures_caught": t.failures_caught,
            "saved": format(t.saved, "f"),
            "total_spend": format(t.total_cost, "f"),
            "failed_spend": format(t.failed_cost, "f"),
            "savings_share": t.savings_share,
            "failed_spend_saved_share": t.failed_spend_saved_share,
            "auroc": t.auroc,
        }
    return out


def _share(flags: Iterable[bool]) -> float | None:
    values = list(flags)
    return sum(values) / len(values) if values else None


def _curve(result: CalibrationResult) -> list[dict[str, float]]:
    evaluations = result.test_evaluations
    if not evaluations:
        return []
    scores = sorted({e.max_score for e in evaluations if e.max_score is not None})
    if len(scores) > CURVE_POINTS:
        step = len(scores) / CURVE_POINTS
        scores = [scores[int(i * step)] for i in range(CURVE_POINTS)] + [scores[-1]]
    thresholds = sorted({*scores, 1.0}, reverse=True)
    return [asdict(p) for p in tradeoff_curve(evaluations, thresholds)]


def s1_verdict(target_090: dict[str, Any]) -> dict[str, Any]:
    """S1: at target recall 0.90 the policy keeps >= 90% of test successes and saves > 0% of
    failed-run spend on the held-out test split. Decided on exact counts and amounts."""
    test = target_090.get("test")
    if test is None:
        return {
            "met": False,
            "why": f"no calibrated policy ({target_090['status']}: {target_090['reason']})",
        }
    n, lost = test["successes"], test["successes_lost"]
    met = 10 * (n - lost) >= 9 * n and Decimal(test["saved"]) > 0
    return {
        "met": met,
        "why": (
            f"kept {(n - lost) / n:.1%} of test successes (>= 90% required), saved "
            f"{test['failed_spend_saved_share']:.1%} of failed-run spend (> 0% required)"
        ),
    }


def evaluate_dataset(dataset: Dataset, runs: Sequence[CompactRun]) -> dict[str, Any]:
    outcomes = Counter(r.outcome for r in runs)
    batches = Counter(r.batch for r in runs)
    doc: dict[str, Any] = {
        "dataset": dataset.key,
        "title": dataset.title,
        "domain": dataset.domain,
        "source": dataset.source,
        "licence": dataset.licence,
        "licence_confirmed": dataset.licence_confirmed,
        "cost_source": dataset.cost_source,
        "cost_unit": dataset.cost_unit,
        "runs": len(runs),
        "successes": outcomes["success"],
        "failures": outcomes["failure"],
        "steps": sum(len(r.steps) for r in runs),
        "batches": dict(sorted(batches.items())),
        "settings": SETTINGS,
        "protocols": {},
    }
    protocols = [PRIMARY, SHUFFLED] + ([SECONDARY] if dataset.has_timestamps else [])
    for protocol in protocols:
        if protocol == SECONDARY:
            ordered = list(runs)
        else:
            seed = SHUFFLE_SEED if protocol == SHUFFLED else None
            ordered = batch_normalized_order(runs, shuffle_seed=seed)
        run_data = [r.to_run_data() for r in ordered]
        split = time_split(run_data)
        train_tasks = {r.group for r in split.train}
        results = {
            t: calibrate(run_data, CalibrationSettings(target_recall=t, **SETTINGS))
            for t in TARGETS
        }
        doc["protocols"][protocol] = {
            "targets": {t: _target_summary(r) for t, r in results.items()},
            "curve": _curve(results[TARGETS[-1]]),
            # How much of the test split repeats a task seen in training: a task-mix shift check.
            "test_runs_with_task_in_train": _share(r.group in train_tasks for r in split.test),
        }
    doc["s1"] = s1_verdict(doc["protocols"][PRIMARY]["targets"]["0.90"])
    return doc
