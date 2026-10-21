"""Training: L2 logistic regression on every decision-point prefix (ARCHITECTURE.md §7.2)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.model_selection import GroupKFold

from sunkcost.core import FEATURE_NAMES, LogisticModel
from sunkcost_worker.calibration.data import RunData

DEFAULT_C_GRID: tuple[float, ...] = tuple(10.0**e for e in range(-4, 5))


class TrainingError(ValueError):
    """The training split cannot produce a model (for example, only one class)."""


@dataclass(frozen=True)
class TrainingRows:
    x: NDArray[np.float64]
    y: NDArray[np.int64]
    weight: NDArray[np.float64]
    group: NDArray[np.int64]
    n_runs: int


@dataclass(frozen=True)
class TrainedModel:
    bundle_model: dict[str, Any]
    """The ``logistic_v1`` block for the bundle; every number is a ``repr(float)`` string."""
    model: LogisticModel
    """Parsed back from ``bundle_model``, so scores are bit-identical to the SDK's."""
    c: float
    cv_log_loss: dict[float, float]
    n_runs: int
    n_rows: int


def training_rows(runs: Sequence[RunData], min_steps: int) -> TrainingRows:
    """One row per decision point k (min_steps <= k <= n - 1) of every run, labelled 1 = failure.

    Each run's rows share a total weight of 1, so long runs do not dominate (ADR-0003).
    """
    xs: list[list[float]] = []
    ys: list[int] = []
    ws: list[float] = []
    gs: list[int] = []
    groups: dict[str, int] = {}
    n_runs = 0
    for run in runs:
        points = run.decision_points(min_steps)
        if not points:
            continue
        n_runs += 1
        vectors = run.feature_vectors()
        label = 1 if run.outcome == "failure" else 0
        weight = 1.0 / len(points)
        group = groups.setdefault(run.group or run.run_id, len(groups))
        for k in points:
            xs.append(vectors[k])
            ys.append(label)
            ws.append(weight)
            gs.append(group)
    width = len(FEATURE_NAMES)
    return TrainingRows(
        x=np.asarray(xs, dtype=np.float64).reshape(-1, width),
        y=np.asarray(ys, dtype=np.int64),
        weight=np.asarray(ws, dtype=np.float64),
        group=np.asarray(gs, dtype=np.int64),
        n_runs=n_runs,
    )


def _fit(
    z: NDArray[np.float64], y: NDArray[np.int64], w: NDArray[np.float64], c: float
) -> LogisticRegression:
    clf = LogisticRegression(C=c, solver="lbfgs", max_iter=2000, tol=1e-8)
    clf.fit(z, y, sample_weight=w)
    return clf


def fit(
    runs: Sequence[RunData],
    min_steps: int,
    *,
    c_grid: Sequence[float] = DEFAULT_C_GRID,
    folds: int = 5,
) -> TrainedModel:
    """Standardise with training means and stds, pick C by grouped k-fold CV, refit on all rows."""
    rows = training_rows(runs, min_steps)
    if rows.x.shape[0] == 0 or len(np.unique(rows.y)) < 2:
        raise TrainingError("training split needs decision points from both successes and failures")
    means = rows.x.mean(axis=0)
    stds = rows.x.std(axis=0)
    stds[stds == 0.0] = 1.0
    z = (rows.x - means) / stds

    n_groups = len(np.unique(rows.group))
    n_folds = min(folds, n_groups)
    cv_loss: dict[float, float] = {}
    if n_folds >= 2:
        splits = list(GroupKFold(n_splits=n_folds).split(z, rows.y, rows.group))
        for c in c_grid:
            losses: list[float] = []
            for train_idx, val_idx in splits:
                if len(np.unique(rows.y[train_idx])) < 2:
                    continue
                clf = _fit(z[train_idx], rows.y[train_idx], rows.weight[train_idx], c)
                proba = clf.predict_proba(z[val_idx])[:, 1]
                losses.append(
                    float(
                        log_loss(
                            rows.y[val_idx],
                            proba,
                            sample_weight=rows.weight[val_idx],
                            labels=[0, 1],
                        )
                    )
                )
            if losses:
                cv_loss[c] = float(np.mean(losses))
    # Lowest mean CV loss; ties go to the smaller C (stronger regularisation).
    best_c = min(cv_loss, key=lambda c: (cv_loss[c], c)) if cv_loss else 1.0

    clf = _fit(z, rows.y, rows.weight, best_c)
    bundle_model = {
        "type": "logistic_v1",
        "intercept": repr(float(clf.intercept_[0])),
        "features": list(FEATURE_NAMES),
        "weights": [repr(float(v)) for v in clf.coef_[0]],
        "means": [repr(float(v)) for v in means],
        "stds": [repr(float(v)) for v in stds],
    }
    return TrainedModel(
        bundle_model=bundle_model,
        model=LogisticModel.from_bundle(bundle_model),
        c=best_c,
        cv_log_loss=cv_loss,
        n_runs=rows.n_runs,
        n_rows=int(rows.x.shape[0]),
    )
