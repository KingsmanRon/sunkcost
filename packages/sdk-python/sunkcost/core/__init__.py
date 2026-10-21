"""Sunkcost core (C5): pure Python, no third-party dependencies.

Shared by the SDK (decision time) and the worker (training time), so both compute identical
features and scores.
"""

from sunkcost.core.args_hash import args_hash
from sunkcost.core.canonical import CanonicalJSONError, canonical_json, ecmascript_number
from sunkcost.core.features import (
    FEATURE_INDEX,
    FEATURE_NAMES,
    FEATURE_SPEC,
    FeatureState,
    StepRecord,
    features_v1,
)
from sunkcost.core.holdout import holdout_bucket, holdout_threshold, is_holdout
from sunkcost.core.scoring import LogisticModel, ModelError, score_logistic_v1, sigmoid
from sunkcost.core.timeutil import datetime_to_us, format_timestamp_us, parse_timestamp_us

__all__ = [
    "FEATURE_INDEX",
    "FEATURE_NAMES",
    "FEATURE_SPEC",
    "CanonicalJSONError",
    "FeatureState",
    "LogisticModel",
    "ModelError",
    "StepRecord",
    "args_hash",
    "canonical_json",
    "datetime_to_us",
    "ecmascript_number",
    "features_v1",
    "format_timestamp_us",
    "holdout_bucket",
    "holdout_threshold",
    "is_holdout",
    "parse_timestamp_us",
    "score_logistic_v1",
    "sigmoid",
]
