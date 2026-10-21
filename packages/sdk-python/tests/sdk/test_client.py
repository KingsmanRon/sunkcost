"""Run lifecycle, emitted events (against the wire contract), pinning and fail-open behaviour."""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from sunkcost import RunAborted, Sunkcost
from sunkcost.core import FeatureState, StepRecord, args_hash, features_v1, parse_timestamp_us
from sunkcost.testing import MemoryTransport, events_of

WORKLOAD = "support.billing_dispute"
Factory = Callable[..., Sunkcost]
Signed = Callable[..., dict[str, Any]]


def failing_steps(run: Any, n: int) -> list[Any]:
    """Check before each of n failing tool steps; returns the decisions."""
    decisions = []
    for _ in range(n):
        d = run.check()
        decisions.append(d)
        if d.abort:
            break
        run.record_step(
            "tool", tool_name="run_tests", tool_args={"cmd": "pytest", "n": 1.5}, error=True
        )
    return decisions


def test_enforced_abort_and_events(
    make_client: Factory, transport: MemoryTransport, signed: Signed, assert_valid_batches: Any
) -> None:
    transport.serve(WORKLOAD, signed(threshold="0.9", min_steps=2))
    client = make_client()
    client.refresh_bundles()
    run = client.start_run(WORKLOAD, external_ref="ZD-1")
    decisions = failing_steps(run, 10)
    assert [d.reason for d in decisions] == [
        "below_min_steps",
        "below_min_steps",
        "above_threshold",
    ]
    run.end("aborted")
    run.outcome("failure", source="zendesk")
    assert client.flush(3)
    assert_valid_batches(transport)

    start, *steps, end, outcome = events_of(transport, run.run_id)
    assert start["type"] == "run_start"
    assert start["mode"] == "enforce"
    assert start["external_ref"] == "ZD-1"
    assert [s["seq"] for s in steps] == [1, 2]
    assert steps[0]["decision"]["reason"] == "below_min_steps"
    assert steps[0]["args_hash"] == args_hash("test-salt", {"cmd": "pytest", "n": 1.5})
    assert (end["status"], end["steps"], end["would_abort_step"], end["aborted_step"]) == (
        "aborted",
        2,
        2,
        2,
    )
    assert end["cost_through_would_abort_usd"] == "0"
    assert outcome == {**outcome, "outcome": "failure", "source": "zendesk"}


def test_features_match_the_worker_reading_of_the_events(
    make_client: Factory, transport: MemoryTransport, signed: Signed
) -> None:
    """The worker rebuilds features from step events; it must see exactly what check() saw."""
    transport.serve(WORKLOAD, signed(threshold="0.99999", min_steps=0, weights={"log_cost": "1.0"}))
    client = make_client()
    client.refresh_bundles()
    run = client.start_run(WORKLOAD)
    scores = []
    for i in range(6):
        scores.append(run.check().score)
        run.record_step(
            "model", model="claude-sonnet-4-5", input_tokens=1000 * i, output_tokens=7, latency_ms=3
        )
    client.flush(3)
    events = events_of(transport, run.run_id)
    started = parse_timestamp_us(events[0]["started_at"])
    steps = [StepRecord.from_wire(e) for e in events if e["type"] == "step"]
    bundle = client.bundles.get(WORKLOAD)[0]
    assert bundle is not None
    for k, score in enumerate(scores):
        assert bundle.model.score(features_v1(started, steps[:k])) == score
    assert [e["decision"]["score"] for e in events if e["type"] == "step"] == [
        repr(s) for s in scores
    ]


def test_cost_from_price_table_and_explicit_cost(
    make_client: Factory, transport: MemoryTransport
) -> None:
    client = make_client()
    run = client.start_run(WORKLOAD)
    run.record_step("model", model="claude-sonnet-4-5", input_tokens=1_000_000, output_tokens=0)
    run.record_step("model", model="not-a-priced-model", input_tokens=5, output_tokens=5)
    run.record_step("tool", tool_name="search", cost_usd="0.002")
    run.record_step("tool", tool_name="search", cost_usd=Decimal("0.0000000004"))
    run.end()
    client.flush(3)
    steps = events_of(transport, run.run_id, ["step"])
    assert [s["cost_usd"] for s in steps] == ["3", None, "0.002", "0"]
    assert events_of(transport, run.run_id, ["run_end"])[0]["total_cost_usd"] == "3.002"
    assert run.total_cost_usd == Decimal("3.002")


def test_run_keeps_its_start_bundle_but_override_applies_at_once(
    make_client: Factory, transport: MemoryTransport, signed: Signed
) -> None:
    transport.serve(WORKLOAD, signed(threshold="0.9", min_steps=0, version=1))
    client = make_client()
    client.refresh_bundles()
    run = client.start_run(WORKLOAD)
    first = run.bundle
    transport.serve(WORKLOAD, signed(threshold="0.9", min_steps=0, version=2, mode="shadow"))
    client.refresh_bundles()
    assert run.bundle is first
    assert client.bundles.get(WORKLOAD)[0].version == 2  # type: ignore[union-attr]
    assert failing_steps(run, 5)[-1].reason == "above_threshold"  # still version 1, enforce

    run2 = client.start_run(WORKLOAD)
    transport.serve(WORKLOAD, signed(threshold="0.9", min_steps=0, version=2, override="shadow"))
    client.refresh_bundles()
    assert failing_steps(run2, 5)[-1].reason == "override_shadow"


def test_holdout_runs_never_abort(
    make_client: Factory, transport: MemoryTransport, signed: Signed
) -> None:
    transport.serve(WORKLOAD, signed(threshold="0.9", min_steps=0, holdout_rate="1"))
    client = make_client()
    client.refresh_bundles()
    run = client.start_run(WORKLOAD)
    assert run.holdout
    decisions = failing_steps(run, 6)
    assert not any(d.abort for d in decisions)
    assert decisions[-1].reason == "holdout"


def test_no_bundle_runs_are_shadow_and_still_report(
    make_client: Factory, transport: MemoryTransport
) -> None:
    client = make_client()
    run = client.start_run(WORKLOAD)
    assert all(d.reason == "no_bundle" for d in failing_steps(run, 4))
    run.end()
    client.flush(3)
    start = events_of(transport, run.run_id, ["run_start"])[0]
    assert (start["mode"], start["bundle_id"], start["holdout"]) == ("shadow", None, False)


def test_context_manager_ends_runs_with_the_right_status(
    make_client: Factory, transport: MemoryTransport
) -> None:
    client = make_client()
    with client.start_run(WORKLOAD) as ok:
        pass
    with pytest.raises(RunAborted), client.start_run(WORKLOAD) as aborted:
        raise RunAborted(ok.check(), aborted.run_id)
    with pytest.raises(KeyError), client.start_run(WORKLOAD) as errored:
        raise KeyError
    client.flush(3)
    status = {
        r.run_id: events_of(transport, r.run_id, ["run_end"])[0]["status"]
        for r in (ok, aborted, errored)
    }
    assert list(status.values()) == ["completed", "aborted", "errored"]


# -- fail-open ------------------------------------------------------------------------------------


def test_internal_exception_in_check_is_sdk_error(
    make_client: Factory,
    transport: MemoryTransport,
    signed: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport.serve(WORKLOAD, signed(threshold="0.0", min_steps=0))
    client = make_client()
    client.refresh_bundles()
    run = client.start_run(WORKLOAD)

    def boom(self: FeatureState) -> list[float]:
        raise RuntimeError("injected")

    monkeypatch.setattr(FeatureState, "vector", boom)
    d = run.check()
    assert (d.action, d.reason) == ("CONTINUE", "sdk_error")
    assert client.stats.sdk_errors == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda r: r.record_step("bogus"),
        lambda r: r.record_step("model", input_tokens="many"),
        lambda r: r.record_step("tool", cost_usd="-3"),
        lambda r: r.end("exploded"),
    ],
)
def test_bad_arguments_never_raise(
    make_client: Factory, transport: MemoryTransport, call: Any
) -> None:
    client = make_client()
    run = client.start_run(WORKLOAD)
    call(run)
    run.end()
    client.flush(3)
    assert client.stats.sdk_errors <= 1


def test_invalid_workload_gives_a_degraded_run(
    make_client: Factory, transport: MemoryTransport
) -> None:
    client = make_client()
    run = client.start_run("not a valid name!")
    assert run.check().reason == "sdk_error"
    run.record_step("tool", tool_name="x")
    run.end()
    client.flush(1)
    assert transport.events == []
    assert client.stats.sdk_errors == 1


def test_invalid_run_id_is_replaced(make_client: Factory) -> None:
    client = make_client()
    run = client.start_run(WORKLOAD, run_id="1234")
    assert run.run_id != "1234"
    assert client.stats.sdk_errors == 1


def test_long_strings_are_truncated_to_the_wire_limits(
    make_client: Factory, transport: MemoryTransport, assert_valid_batches: Any
) -> None:
    client = make_client()
    run = client.start_run(WORKLOAD, external_ref="r" * 500)
    run.record_step("tool", tool_name="t" * 1000, latency_ms=10**12, input_tokens=-5)
    run.end()
    client.flush(3)
    assert_valid_batches(transport)


def test_offline_client_decides_without_network() -> None:
    client = Sunkcost(args_hash_salt="s")
    run = client.start_run(WORKLOAD)
    assert run.check().reason == "no_bundle"
    run.record_step("tool", tool_name="x")
    run.end()
    assert client.stats.events_discarded_offline == 3
    assert client.close()


def test_check_makes_no_transport_call(
    make_client: Factory, transport: MemoryTransport, signed: Signed
) -> None:
    transport.serve(WORKLOAD, signed(threshold="0.9"))
    client = make_client(bundle_refresh_s=3600)
    client.refresh_bundles()
    fetches = transport.fetches
    run = client.start_run(WORKLOAD)
    batches = len(transport.batches)
    t0 = time.monotonic()
    for _ in range(1000):
        run.check()
    assert time.monotonic() - t0 < 1.0
    assert transport.fetches == fetches
    assert len(transport.batches) == batches
