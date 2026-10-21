"""The decision engine: every reason, their precedence, and the decision.v1 contract."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from sunkcost.bundle import Bundle, load_trusted_keys, verify_response
from sunkcost.core import FeatureState, StepRecord
from sunkcost.decision import decide
from sunkcost.testing import TEST_TRUSTED_KEYS, make_bundle, sign
from sunkcost_spec.validation import model_errors, schema_errors

KEYS = load_trusted_keys(TEST_TRUSTED_KEYS)
WORKLOAD = "support.billing_dispute"


def now_us() -> int:
    return time.time_ns() // 1000


def signed(**kwargs: Any) -> dict[str, Any]:
    override = kwargs.pop("override", None)
    kwargs.setdefault("issued_at_us", now_us())
    return sign(make_bundle(WORKLOAD, **kwargs), override=override)


def bundle(**kwargs: Any) -> Bundle:
    parsed, _ = verify_response(signed(**kwargs), KEYS, workload=WORKLOAD, now_us=now_us())
    return parsed


def state(errors: int, start: int = 0) -> FeatureState:
    s = FeatureState(start)
    for i in range(errors):
        s.update(StepRecord("tool", None, "t", None, 0, 0, 0.0, True, start + i))
    return s


def check_contract(d: Any) -> None:
    doc = d.to_json()
    assert schema_errors("decision.v1", doc) == []
    assert model_errors("decision.v1", json.dumps(doc)) == []


# error_streak weight 2.0, intercept 0: score = sigmoid(2 * errors); threshold 0.9 is crossed at 2.
@pytest.mark.parametrize(
    ("kwargs", "override", "holdout", "errors", "action", "reason", "would_abort", "mode"),
    [
        ({}, None, False, 1, "CONTINUE", "below_min_steps", False, "enforce"),
        ({"min_steps": 0}, None, False, 1, "CONTINUE", "below_threshold", False, "enforce"),
        ({"min_steps": 0}, None, False, 3, "ABORT", "above_threshold", True, "enforce"),
        ({"min_steps": 0}, "shadow", False, 3, "CONTINUE", "override_shadow", True, "shadow"),
        (
            {"min_steps": 0, "mode": "shadow"},
            None,
            False,
            3,
            "CONTINUE",
            "shadow_mode",
            True,
            "shadow",
        ),
        ({"min_steps": 0}, None, True, 3, "CONTINUE", "holdout", True, "enforce"),
        # Precedence: an override beats shadow mode and holdout.
        (
            {"min_steps": 0, "mode": "shadow"},
            "shadow",
            True,
            3,
            "CONTINUE",
            "override_shadow",
            True,
            "shadow",
        ),
        (
            {"min_steps": 0, "mode": "shadow"},
            None,
            True,
            3,
            "CONTINUE",
            "shadow_mode",
            True,
            "shadow",
        ),
    ],
)
def test_reasons(
    kwargs: dict[str, Any],
    override: str | None,
    holdout: bool,
    errors: int,
    action: str,
    reason: str,
    would_abort: bool,
    mode: str,
) -> None:
    b = bundle(threshold="0.9", **kwargs)
    d = decide(b, override, holdout, state(errors), now_us())
    assert (d.action, d.reason, d.would_abort, d.mode) == (action, reason, would_abort, mode)
    assert d.bundle_id == b.bundle_id
    assert d.bundle_version == b.version
    check_contract(d)


def test_no_bundle() -> None:
    d = decide(None, None, False, state(5), now_us())
    assert (d.action, d.reason, d.score, d.threshold, d.mode) == (
        "CONTINUE",
        "no_bundle",
        None,
        None,
        "shadow",
    )
    check_contract(d)


def test_expired_bundle_never_aborts() -> None:
    b = bundle(threshold="0.1", min_steps=0, ttl_s=60)
    d = decide(b, None, False, state(5), b.expires_at_us)
    assert (d.action, d.reason, d.would_abort, d.mode) == (
        "CONTINUE",
        "bundle_expired",
        False,
        "shadow",
    )
    check_contract(d)


def test_threshold_is_inclusive() -> None:
    """would_abort iff score >= threshold, the same comparison calibration uses (m_i >= τ)."""
    b = bundle(threshold="0.5", min_steps=0, weights={"error_streak": "0"})  # score is exactly 0.5
    assert decide(b, None, False, state(0), now_us()).action == "ABORT"


def test_score_matches_core() -> None:
    b = bundle(threshold="0.9", min_steps=0)
    s = state(2)
    assert decide(b, None, False, s, now_us()).score == b.model.score(s.vector())
