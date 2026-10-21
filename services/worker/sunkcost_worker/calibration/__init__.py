"""Calibration library (ARCHITECTURE.md §7.2-§7.4) for the worker's calibrate job and bench/."""

from sunkcost_worker.calibration.accounting import AccountedRun, SavingsReport, account
from sunkcost_worker.calibration.calibrate import (
    CalibrationResult,
    CalibrationSettings,
    Split,
    calibrate,
    time_split,
)
from sunkcost_worker.calibration.data import RunData, is_uncensored, labelled_uncensored
from sunkcost_worker.calibration.evaluate import (
    CurvePoint,
    RunEvaluation,
    TestMetrics,
    evaluate_run,
    evaluate_test,
    summarize,
    tradeoff_curve,
)
from sunkcost_worker.calibration.stats import cp_interval, cp_lower, cp_upper
from sunkcost_worker.calibration.threshold import (
    ThresholdResult,
    max_decision_score,
    select_threshold,
)
from sunkcost_worker.calibration.train import TrainedModel, TrainingError, fit, training_rows

__all__ = [
    "AccountedRun",
    "CalibrationResult",
    "CalibrationSettings",
    "CurvePoint",
    "RunData",
    "RunEvaluation",
    "SavingsReport",
    "Split",
    "TestMetrics",
    "ThresholdResult",
    "TrainedModel",
    "TrainingError",
    "account",
    "calibrate",
    "cp_interval",
    "cp_lower",
    "cp_upper",
    "evaluate_run",
    "evaluate_test",
    "fit",
    "is_uncensored",
    "labelled_uncensored",
    "max_decision_score",
    "select_threshold",
    "summarize",
    "time_split",
    "tradeoff_curve",
    "training_rows",
]
