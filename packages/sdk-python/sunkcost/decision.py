"""The decision engine: a pure function of the run's bundle, its feature state and the clock.

Order of evaluation (ADR-0004):

1. no bundle for this run                → CONTINUE, ``no_bundle`` (no score)
2. the run's bundle has expired          → CONTINUE, ``bundle_expired`` (no score)
3. fewer than ``min_steps`` completed    → CONTINUE, ``below_min_steps``
4. score < threshold                     → CONTINUE, ``below_threshold``
5. score ≥ threshold (``would_abort``): an override → ``override_shadow``; a shadow bundle →
   ``shadow_mode``; a holdout run → ``holdout``; otherwise ABORT, ``above_threshold``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sunkcost.bundle import Bundle
from sunkcost.core import FeatureState


@dataclass(frozen=True, slots=True)
class Decision:
    action: str  # "CONTINUE" | "ABORT"
    mode: str  # effective mode: "shadow" | "enforce"
    score: float | None
    threshold: float | None
    bundle_id: str | None
    bundle_version: int | None
    would_abort: bool
    reason: str

    @property
    def abort(self) -> bool:
        return self.action == "ABORT"

    def to_json(self) -> dict[str, Any]:
        """``decision.v1`` form: floats as shortest round-trip decimal strings."""
        return {
            "schema_version": "1",
            "action": self.action,
            "mode": self.mode,
            "score": None if self.score is None else repr(self.score),
            "threshold": None if self.threshold is None else repr(self.threshold),
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "would_abort": self.would_abort,
            "reason": self.reason,
        }

    def step_json(self) -> dict[str, Any]:
        """The subset carried on a step event (``decision.v1#/$defs/step_decision``)."""
        return {
            "action": self.action,
            "score": None if self.score is None else repr(self.score),
            "threshold": None if self.threshold is None else repr(self.threshold),
            "would_abort": self.would_abort,
            "reason": self.reason,
        }


NO_BUNDLE = Decision("CONTINUE", "shadow", None, None, None, None, False, "no_bundle")
SDK_ERROR = Decision("CONTINUE", "shadow", None, None, None, None, False, "sdk_error")


def effective_mode(bundle: Bundle | None, override: str | None, now_us: int) -> str:
    if bundle is None or override is not None or bundle.expires_at_us <= now_us:
        return "shadow"
    return bundle.mode


def decide(
    bundle: Bundle | None,
    override: str | None,
    holdout: bool,
    state: FeatureState,
    now_us: int,
) -> Decision:
    if bundle is None:
        return NO_BUNDLE
    bid, version = bundle.bundle_id, bundle.version
    if bundle.expires_at_us <= now_us:
        return Decision("CONTINUE", "shadow", None, None, bid, version, False, "bundle_expired")
    mode = "shadow" if override is not None else bundle.mode
    score = bundle.model.score(state.vector())
    threshold = bundle.threshold
    if state.k < bundle.min_steps:
        return Decision("CONTINUE", mode, score, threshold, bid, version, False, "below_min_steps")
    if score < threshold:
        return Decision("CONTINUE", mode, score, threshold, bid, version, False, "below_threshold")
    if override is not None:
        reason = "override_shadow"
    elif bundle.mode == "shadow":
        reason = "shadow_mode"
    elif holdout:
        reason = "holdout"
    else:
        return Decision("ABORT", mode, score, threshold, bid, version, True, "above_threshold")
    return Decision("CONTINUE", mode, score, threshold, bid, version, True, reason)
