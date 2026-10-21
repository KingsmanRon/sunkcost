"""Features v1, maintained incrementally (packages/spec/features/features.v1.md is normative).

``FeatureState.update(step)`` is O(1) amortised and ``FeatureState.vector()`` is O(1), so a
``check()`` costs the same at step 200 as at step 2. The same code computes features in the SDK at
decision time and in the worker at training time; the golden vectors in ``packages/spec/vectors``
pin both to an independent reference implementation.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sunkcost.core.timeutil import parse_timestamp_us

FEATURE_SPEC = "features.v1"

FEATURE_NAMES: tuple[str, ...] = (
    "step_index",
    "log_cost",
    "log_tokens",
    "recent_cost_share",
    "repeat_action_rate",
    "max_identical_streak",
    "error_rate",
    "error_streak",
    "distinct_action_ratio",
    "cycle_rate",
    "context_growth",
    "log_elapsed_s",
)
FEATURE_INDEX: dict[str, int] = {name: i for i, name in enumerate(FEATURE_NAMES)}

_RECENT_WINDOW = 3
_REPEAT_WINDOW = 5
_MIN_COST_DENOMINATOR = 1e-12

Action = tuple[str, str, str]
State = tuple[str, str]


@dataclass(frozen=True, slots=True)
class StepRecord:
    """The parts of a completed step that the features use.

    ``cost`` is the step's ``cost_usd`` as the nearest float64 (``0.0`` when unknown); ``ts_us`` is
    the step's completion timestamp in microseconds since the epoch.
    """

    kind: str
    model: str | None
    tool_name: str | None
    args_hash: str | None
    input_tokens: int
    output_tokens: int
    cost: float
    error: bool
    ts_us: int

    @classmethod
    def from_wire(cls, event: Mapping[str, Any]) -> StepRecord:
        """Build from an ingest-batch ``step`` event (a decoded JSON object)."""
        cost_usd = event["cost_usd"]
        return cls(
            kind=event["kind"],
            model=event["model"],
            tool_name=event["tool_name"],
            args_hash=event["args_hash"],
            input_tokens=event["input_tokens"],
            output_tokens=event["output_tokens"],
            cost=0.0 if cost_usd is None else float(cost_usd),
            error=event["error"],
            ts_us=parse_timestamp_us(event["ts"]),
        )

    @property
    def action(self) -> Action:
        name = self.tool_name if self.tool_name is not None else (self.model or "")
        return (self.kind, name, self.args_hash or "")


class FeatureState:
    """Running features.v1 state for one run."""

    __slots__ = (
        "_actions_seen",
        "_best_streak",
        "_cost",
        "_error_streak",
        "_errors",
        "_first_model_input",
        "_k",
        "_last_action",
        "_last_model_input",
        "_last_state",
        "_last_ts_us",
        "_recent_costs",
        "_repeat_flags",
        "_repeated_transitions",
        "_started_at_us",
        "_streak",
        "_tokens",
        "_transitions_seen",
    )

    def __init__(self, started_at_us: int) -> None:
        self._started_at_us = started_at_us
        self._k = 0
        self._cost = 0.0
        self._tokens = 0
        self._recent_costs: deque[float] = deque(maxlen=_RECENT_WINDOW)
        self._actions_seen: set[Action] = set()
        self._repeat_flags: deque[bool] = deque(maxlen=_REPEAT_WINDOW)
        self._last_action: Action | None = None
        self._streak = 0
        self._best_streak = 0
        self._errors = 0
        self._error_streak = 0
        self._last_state: State | None = None
        self._transitions_seen: set[tuple[State, State]] = set()
        self._repeated_transitions = 0
        self._first_model_input: int | None = None
        self._last_model_input = 0
        self._last_ts_us = started_at_us

    @property
    def k(self) -> int:
        """Number of completed steps folded in so far."""
        return self._k

    @property
    def total_cost(self) -> float:
        return self._cost

    def update(self, step: StepRecord) -> None:
        """Fold in one completed step."""
        self._k += 1
        self._cost += step.cost
        self._tokens += step.input_tokens + step.output_tokens
        self._recent_costs.append(step.cost)

        action = step.action
        self._repeat_flags.append(action in self._actions_seen)
        self._actions_seen.add(action)
        self._streak = self._streak + 1 if action == self._last_action else 1
        self._best_streak = max(self._best_streak, self._streak)
        self._last_action = action

        if step.error:
            self._errors += 1
            self._error_streak += 1
        else:
            self._error_streak = 0

        state: State = (action[0], action[1])
        if self._last_state is not None:
            transition = (self._last_state, state)
            if transition in self._transitions_seen:
                self._repeated_transitions += 1
            else:
                self._transitions_seen.add(transition)
        self._last_state = state

        if step.kind == "model":
            if self._first_model_input is None:
                self._first_model_input = step.input_tokens
            self._last_model_input = step.input_tokens

        self._last_ts_us = step.ts_us

    def vector(self) -> list[float]:
        """The 12 features for the current prefix, in ``FEATURE_NAMES`` order."""
        k = self._k
        if k == 0:
            recent_share = 0.0
            repeat_rate = 0.0
            log_elapsed = 0.0
        else:
            window = 0.0
            for c in self._recent_costs:
                window += c
            recent_share = window / max(self._cost, _MIN_COST_DENOMINATOR)
            repeat_rate = float(sum(self._repeat_flags)) / float(min(_REPEAT_WINDOW, k))
            elapsed = float(self._last_ts_us - self._started_at_us) / 1000000.0
            log_elapsed = math.log1p(max(0.0, elapsed))

        cycle_rate = 0.0 if k < 2 else float(self._repeated_transitions) / float(k - 1)

        if self._first_model_input is None:
            context_growth = 0.0
        else:
            context_growth = math.log1p(float(self._last_model_input)) - math.log1p(
                float(self._first_model_input)
            )

        denominator = float(max(k, 1))
        return [
            float(k),
            math.log1p(self._cost),
            math.log1p(float(self._tokens)),
            recent_share,
            repeat_rate,
            float(self._best_streak),
            float(self._errors) / denominator,
            float(self._error_streak),
            float(len(self._actions_seen)) / denominator,
            cycle_rate,
            context_growth,
            log_elapsed,
        ]


def features_v1(started_at_us: int, prefix: Iterable[StepRecord]) -> list[float]:
    """Features for a whole prefix at once (convenience wrapper over ``FeatureState``)."""
    state = FeatureState(started_at_us)
    for step in prefix:
        state.update(step)
    return state.vector()
