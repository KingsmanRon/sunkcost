"""Runs as the calibration library sees them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sunkcost.core import FeatureState, StepRecord

Outcome = Literal["success", "failure", "abandoned"]
Mode = Literal["shadow", "enforce"]


@dataclass(frozen=True)
class RunData:
    """One run with its completed steps, in order.

    ``costs`` are the steps' exact USD amounts (unknown costs as 0) and are used for every money
    figure. The features use ``StepRecord.cost``, the float64 of the same wire value.
    """

    run_id: str
    started_at_us: int
    steps: tuple[StepRecord, ...]
    costs: tuple[Decimal, ...]
    outcome: Outcome | None
    mode: Mode = "shadow"
    holdout: bool = False
    aborted_step: int | None = None
    group: str | None = None  # e.g. a task id, for grouped cross-validation; defaults to run_id
    _vectors: list[list[float]] | None = field(default=None, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.costs) != len(self.steps):
            raise ValueError("costs and steps must have the same length")

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    @property
    def total_cost(self) -> Decimal:
        return sum(self.costs, Decimal(0))

    def cost_through(self, k: int) -> Decimal:
        """Cost of steps 1..k."""
        return sum(self.costs[:k], Decimal(0))

    def feature_vectors(self) -> list[list[float]]:
        """Feature vectors for every prefix k = 0..n (index k), computed once and cached."""
        vectors = self._vectors
        if vectors is None:
            state = FeatureState(self.started_at_us)
            vectors = [state.vector()]
            for step in self.steps:
                state.update(step)
                vectors.append(state.vector())
            object.__setattr__(self, "_vectors", vectors)
        return vectors

    def decision_points(self, min_steps: int) -> range:
        """Prefix lengths at which check() runs and may abort: min_steps <= k <= n - 1.

        check() is called before each step, so the last check sees n - 1 completed steps; there is
        no decision after the final step (ADR-0003).
        """
        return range(min_steps, self.n_steps)


def is_uncensored(run: RunData) -> bool:
    """Shadow runs and enforce-mode holdout runs, never aborted (ARCHITECTURE.md §7.3)."""
    if run.aborted_step is not None:
        return False
    return run.mode == "shadow" or run.holdout


def labelled_uncensored(runs: Sequence[RunData]) -> list[RunData]:
    """Runs usable for calibration: uncensored, with a final success or failure outcome."""
    return [r for r in runs if is_uncensored(r) and r.outcome in ("success", "failure")]
