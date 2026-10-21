"""Features and scores against the normative golden vectors, and against the reference itself."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sunkcost.core import (
    FEATURE_NAMES,
    FeatureState,
    LogisticModel,
    ModelError,
    StepRecord,
    features_v1,
    format_timestamp_us,
    parse_timestamp_us,
    score_logistic_v1,
)
from sunkcost_spec.resources import VECTORS_DIR

VECTORS = sorted((VECTORS_DIR / "features.v1").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return doc


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_incremental_features_and_scores_match_vectors_exactly(path: Path) -> None:
    # The contract is a relative tolerance of 1e-12; the Python core is held to bit equality,
    # which is stronger and catches any change in the order of operations.
    doc = _load(path)
    assert tuple(doc["feature_names"]) == FEATURE_NAMES
    model = LogisticModel.from_bundle(doc["model"])
    state = FeatureState(parse_timestamp_us(doc["started_at"]))
    steps = [StepRecord.from_wire(s) for s in doc["steps"]]
    for expected in doc["expected"]:
        k = expected["k"]
        if k > 0:
            state.update(steps[k - 1])
        x = state.vector()
        assert [repr(v) for v in x] == expected["features"], f"k={k}"
        assert repr(model.score(x)) == expected["score"], f"k={k}"


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_batch_form_matches_incremental(path: Path) -> None:
    doc = _load(path)
    start = parse_timestamp_us(doc["started_at"])
    steps = [StepRecord.from_wire(s) for s in doc["steps"]]
    for expected in doc["expected"]:
        x = features_v1(start, steps[: expected["k"]])
        assert [repr(v) for v in x] == expected["features"]


def test_score_accepts_raw_bundle_model() -> None:
    doc = _load(VECTORS_DIR / "features.v1" / "tool-loop.json")
    x = [float(v) for v in doc["expected"][-1]["features"]]
    assert repr(score_logistic_v1(doc["model"], x)) == doc["expected"][-1]["score"]


# --- cross-check against the reference implementation on random runs --------------------------

_NAMES = st.sampled_from(["search", "fetch", "edit", "run_tests", None])
_HASHES = st.sampled_from([None, "hmac-sha256:" + "a" * 64, "hmac-sha256:" + "b" * 64])
_COSTS = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=5_000_000_000).map(lambda n: f"{n / 1e9:.9f}"),
)


@st.composite
def _runs(draw: st.DrawFn) -> tuple[str, list[dict[str, Any]]]:
    start = draw(st.integers(min_value=1_700_000_000_000_000, max_value=1_800_000_000_000_000))
    t = start
    steps: list[dict[str, Any]] = []
    for seq in range(1, draw(st.integers(min_value=0, max_value=40)) + 1):
        t += draw(st.integers(min_value=-2_000_000, max_value=90_000_000))
        kind = draw(st.sampled_from(["model", "tool"]))
        steps.append(
            {
                "type": "step",
                "run_id": "01a0cdb5-3500-7a3c-9e41-5b2f8c7d6e10",
                "seq": seq,
                "ts": format_timestamp_us(t),
                "kind": kind,
                "model": draw(st.sampled_from(["m1", "m2", None])),
                "tool_name": draw(_NAMES),
                "args_hash": draw(_HASHES),
                "input_tokens": draw(st.integers(min_value=0, max_value=2_000_000)),
                "output_tokens": draw(st.integers(min_value=0, max_value=50_000)),
                "cost_usd": draw(_COSTS),
                "error": draw(st.booleans()),
                "latency_ms": None,
                "decision": None,
            }
        )
    return format_timestamp_us(start), steps


@settings(max_examples=300, deadline=None)
@given(run=_runs())
def test_matches_reference_on_random_runs(
    reference: ModuleType, run: tuple[str, list[Any]]
) -> None:
    started_at, steps = run
    state = FeatureState(parse_timestamp_us(started_at))
    records = [StepRecord.from_wire(s) for s in steps]
    for k in range(len(steps) + 1):
        if k:
            state.update(records[k - 1])
        assert state.vector() == reference.features_at(started_at, steps, k)


# --- model validation -----------------------------------------------------------------------


def _model(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "logistic_v1",
        "intercept": "0.5",
        "features": ["step_index", "error_rate"],
        "weights": ["1.0", "2.0"],
        "means": ["0.0", "0.1"],
        "stds": ["1.0", "0.5"],
    }
    base.update(changes)
    return base


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "logistic_v2"},
        {"features": ["step_index", "prompt_length"]},
        {"features": ["step_index", "step_index"]},
        {"stds": ["1.0", "0.0"]},
        {"stds": ["1.0", "-1.0"]},
        {"weights": ["1.0"]},
        {"weights": [1.0, 2.0]},
        {"intercept": "nan"},
        {"intercept": "1e999"},
        {"features": [], "weights": [], "means": [], "stds": []},
        {"means": "0.0"},
    ],
)
def test_invalid_models_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ModelError):
        LogisticModel.from_bundle(_model(**changes))


def test_sigmoid_limits() -> None:
    model = LogisticModel.from_bundle(_model(intercept="-800.0", weights=["0.0", "0.0"]))
    assert model.score([0.0] * 12) == 0.0
    model = LogisticModel.from_bundle(_model(intercept="800.0", weights=["0.0", "0.0"]))
    assert model.score([0.0] * 12) == 1.0
