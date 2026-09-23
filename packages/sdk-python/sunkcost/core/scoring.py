"""The ``logistic_v1`` score (features.v1.md, "Score").

``z = intercept + Σ_j w_j · ((x_j − μ_j) / σ_j)``, with the sum formed first, and a sigmoid that
never overflows. Model parameters arrive as float64 decimal strings from the signed bundle.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sunkcost.core.features import FEATURE_INDEX


class ModelError(ValueError):
    """The bundle's model block cannot be evaluated by this implementation."""


def _finite(value: Any, what: str) -> float:
    if not isinstance(value, str):
        raise ModelError(f"{what} must be a decimal string")
    try:
        number = float(value)
    except ValueError as exc:
        raise ModelError(f"{what} is not a number: {value!r}") from exc
    if not math.isfinite(number):
        raise ModelError(f"{what} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class LogisticModel:
    intercept: float
    feature_indices: tuple[int, ...]
    weights: tuple[float, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]

    @classmethod
    def from_bundle(cls, model: Mapping[str, Any]) -> LogisticModel:
        """Validate and parse a bundle's ``model`` object."""
        if model.get("type") != "logistic_v1":
            raise ModelError(f"unsupported model type {model.get('type')!r}")
        names = model.get("features")
        weights, means, stds = model.get("weights"), model.get("means"), model.get("stds")
        if not (
            isinstance(names, list)
            and isinstance(weights, list)
            and isinstance(means, list)
            and isinstance(stds, list)
        ):
            raise ModelError("features, weights, means and stds must be arrays")
        if not names or not len(names) == len(weights) == len(means) == len(stds):
            raise ModelError("features, weights, means and stds must be non-empty and equal length")
        if len(set(names)) != len(names):
            raise ModelError("duplicate feature names")
        indices = []
        for name in names:
            if name not in FEATURE_INDEX:
                raise ModelError(f"unknown feature {name!r}")
            indices.append(FEATURE_INDEX[name])
        parsed_stds = tuple(_finite(s, "std") for s in stds)
        if any(s <= 0.0 for s in parsed_stds):
            raise ModelError("every std must be > 0")
        return cls(
            intercept=_finite(model.get("intercept"), "intercept"),
            feature_indices=tuple(indices),
            weights=tuple(_finite(w, "weight") for w in weights),
            means=tuple(_finite(m, "mean") for m in means),
            stds=parsed_stds,
        )

    def score(self, x: Sequence[float]) -> float:
        s = 0.0
        for i, w, mean, std in zip(
            self.feature_indices, self.weights, self.means, self.stds, strict=True
        ):
            s = s + w * ((x[i] - mean) / std)
        return sigmoid(self.intercept + s)


def sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def score_logistic_v1(model: LogisticModel | Mapping[str, Any], x: Sequence[float]) -> float:
    """Score a feature vector (``FEATURE_NAMES`` order) with a parsed or raw bundle model."""
    parsed = model if isinstance(model, LogisticModel) else LogisticModel.from_bundle(model)
    return parsed.score(x)
