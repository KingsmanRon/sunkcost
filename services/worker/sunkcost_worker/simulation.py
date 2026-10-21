"""G2b: does the recall guarantee hold across many workloads with known failure processes?

Each simulated workload draws a random failure process, generates a history, and runs the real
``calibrate()``. It then measures the realised false-abort rate of the chosen threshold on a large
fresh sample of successful runs from the same process. The guarantee says the share of workloads
whose realised rate exceeds α is at most δ; the gate allows δ + 0.03 for Monte Carlo error.
"""

from __future__ import annotations

import os
import random
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

from threadpoolctl import threadpool_limits

from sunkcost_worker.calibration import CalibrationSettings, calibrate
from sunkcost_worker.synthetic import WorkloadProcess, generate_history, generate_run

# (target_recall, delta) cells; workloads cycle through them.
CELLS: tuple[tuple[str, str], ...] = (
    ("0.95", "0.05"),
    ("0.95", "0.10"),
    ("0.90", "0.05"),
    ("0.90", "0.10"),
)
MIN_STEPS_CHOICES = (0, 2, 3, 5)


@dataclass(frozen=True)
class SimulationResult:
    index: int
    target_recall: str
    delta: str
    min_steps: int
    status: str
    threshold: float | None
    calib_false_aborts: int | None
    n_calib_success: int | None
    future_successes: int
    future_false_aborts: int

    @property
    def alpha(self) -> float:
        return 1.0 - float(self.target_recall)

    @property
    def future_rate(self) -> float:
        return self.future_false_aborts / self.future_successes if self.future_successes else 0.0

    @property
    def exceeds(self) -> bool:
        return self.threshold is not None and self.future_rate > self.alpha


def simulate(
    index: int, seed: int, history_runs: int = 1200, future_successes: int = 5000
) -> SimulationResult:
    with threadpool_limits(limits=1):
        rng = random.Random(seed * 1_000_003 + index)  # noqa: S311 - seeded simulation, not crypto
        process = WorkloadProcess.sample(rng)
        target_recall, delta = CELLS[index % len(CELLS)]
        min_steps = rng.choice(MIN_STEPS_CHOICES)
        history = generate_history(rng, process, history_runs)
        result = calibrate(
            history,
            CalibrationSettings(target_recall=target_recall, delta=delta, min_steps=min_steps),
        )
        tau = None if result.threshold is None else result.threshold.threshold
        false_aborts = 0
        if tau is not None and result.trained is not None:
            model = result.trained.model
            start = history[-1].started_at_us + 60_000_000
            for j in range(future_successes):
                run = generate_run(rng, process, start + j * 30_000_000, outcome="success")
                vectors = run.feature_vectors()
                if any(model.score(vectors[k]) >= tau for k in run.decision_points(min_steps)):
                    false_aborts += 1
        return SimulationResult(
            index=index,
            target_recall=target_recall,
            delta=delta,
            min_steps=min_steps,
            status=result.status,
            threshold=tau,
            calib_false_aborts=None if result.threshold is None else result.threshold.false_aborts,
            n_calib_success=result.counts.get("calibration_success"),
            future_successes=future_successes if tau is not None else 0,
            future_false_aborts=false_aborts,
        )


def run_simulations(
    n: int,
    seed: int,
    *,
    workers: int | None = None,
    history_runs: int = 1200,
    future_successes: int = 5000,
) -> list[SimulationResult]:
    workers = workers or os.cpu_count() or 1
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(simulate, i, seed, history_runs, future_successes) for i in range(n)]
        return [f.result() for f in futures]


def summarize(results: Sequence[SimulationResult]) -> dict[str, object]:
    cells = []
    for target_recall, delta in CELLS:
        cell = [r for r in results if (r.target_recall, r.delta) == (target_recall, delta)]
        with_tau = [r for r in cell if r.threshold is not None]
        rates = [r.future_rate for r in with_tau]
        exceed = sum(1 for r in with_tau if r.exceeds)
        cells.append(
            {
                "target_recall": target_recall,
                "delta": delta,
                "alpha": round(1 - float(target_recall), 10),
                "workloads": len(cell),
                "with_threshold": len(with_tau),
                "validated": sum(1 for r in cell if r.status == "validated"),
                "exceeding_alpha": exceed,
                "exceed_share": exceed / len(with_tau) if with_tau else 0.0,
                "allowed_share": float(delta) + 0.03,
                "future_rate_mean": statistics.fmean(rates) if rates else None,
                "future_rate_p95": sorted(rates)[int(0.95 * (len(rates) - 1))] if rates else None,
                "future_rate_max": max(rates) if rates else None,
            }
        )
    return {
        "workloads": len(results),
        "statuses": {
            s: sum(1 for r in results if r.status == s) for s in sorted({r.status for r in results})
        },
        "cells": cells,
        "results": [asdict(r) for r in results],
    }
