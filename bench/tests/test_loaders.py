"""Conversion rules for each dataset, on small hand-written trajectories."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from sunkcost.core import StepRecord, args_hash
from sunkcost_bench.common import (
    ARGS_SALT,
    ARRIVAL_EPOCH_US,
    CompactRun,
    batch_normalized_order,
    git_blob_sha1,
    hash_args,
    money,
)
from sunkcost_bench.datasets import swebench, tau2, taubench
from sunkcost_bench.evaluate import s1_verdict


def test_git_blob_sha1_matches_git() -> None:
    assert git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_hash_args_decodes_json_strings() -> None:
    assert hash_args('{"b": 1, "a": 2}') == args_hash(ARGS_SALT, {"a": 2, "b": 1})
    assert hash_args("not json") == args_hash(ARGS_SALT, "not json")


def test_money_is_exact_to_nine_places() -> None:
    assert money(0.002428) == Decimal("0.002428000")
    assert money(None) == Decimal(0)
    assert format(money(0), "f") == "0.000000000"


def _tau2_sim(reward: float) -> dict[str, Any]:
    return {
        "id": "sim-1",
        "task_id": "7",
        "start_time": "2025-06-05T15:34:00.000000",
        "reward_info": {"reward": reward},
        "messages": [
            {
                "role": "assistant",
                "content": "Hi!",
                "usage": None,
                "cost": 0.0,
                "timestamp": "2025-06-05T15:34:00.100000",
            },
            {"role": "user", "content": "help", "timestamp": "2025-06-05T15:34:01.000000"},
            {
                "role": "assistant",
                "usage": {"prompt_tokens": 1200, "completion_tokens": 40},
                "cost": 0.002428,
                "timestamp": "2025-06-05T15:34:02.000000",
                "tool_calls": [{"id": "c1", "name": "get_order", "arguments": {"order_id": "#1"}}],
            },
            {
                "role": "tool",
                "id": "c1",
                "requestor": "assistant",
                "error": True,
                "timestamp": "2025-06-05T15:34:02.500000",
            },
            {
                "role": "tool",
                "id": "u1",
                "requestor": "user",
                "error": False,
                "timestamp": "2025-06-05T15:34:03.000000",
            },
        ],
    }


def test_tau2_conversion() -> None:
    run = tau2.convert(_tau2_sim(1.0), "gpt-4.1-2025-04-14", "retail")
    assert run is not None
    assert run.outcome == "success"
    assert [s.kind for s in run.steps] == [
        "model",
        "tool",
    ]  # greeting and user-side tool call skipped
    model, tool = run.steps
    assert (model.model, model.input_tokens, model.output_tokens) == (
        "gpt-4.1-2025-04-14",
        1200,
        40,
    )
    assert run.costs == (Decimal("0.002428000"), Decimal(0))
    assert (tool.tool_name, tool.error) == ("get_order", True)
    assert tool.args_hash == args_hash(ARGS_SALT, {"order_id": "#1"})
    assert tool.ts_us - run.started_at_us == 2_500_000
    assert tau2.convert(_tau2_sim(0.5), "gpt-4.1-2025-04-14", "retail") is None


def test_tau_bench_conversion_uses_unit_costs() -> None:
    record = {
        "task_id": 3,
        "trial": 1,
        "reward": 0.0,
        "traj": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "0", "function": {"name": "find", "arguments": '{"x": 1}'}}],
            },
            {
                "role": "tool",
                "tool_call_id": "0",
                "name": "find",
                "content": "Error: user not found",
            },
            {"role": "assistant", "content": "Sorry"},
        ],
    }
    run = taubench.convert(record, "gpt-4o", "retail", started_at_us=5)
    assert run is not None
    assert run.outcome == "failure"
    assert [s.kind for s in run.steps] == ["model", "tool", "model"]
    assert run.costs == (Decimal(1),) * 3
    assert run.steps[1].error
    assert run.steps[1].args_hash == args_hash(ARGS_SALT, {"x": 1})
    assert all(s.ts_us == 5 for s in run.steps)


def test_swe_bench_chat_format() -> None:
    traj = {
        "instance_id": "astropy__astropy-1",
        "messages": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "t1", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}
                ],
                "extra": {
                    "cost": 0.0129,
                    "timestamp": 1771235425.85,
                    "response": {
                        "model": "claude-x",
                        "created": 1771235424,
                        "usage": {"prompt_tokens": 2214, "completion_tokens": 309},
                    },
                },
            },
            {
                "role": "tool",
                "tool_call_id": "t1",
                "extra": {"returncode": "1", "timestamp": 1771235426.2, "exception_info": ""},
            },
            {"role": "exit", "content": "diff"},
        ],
    }
    run = swebench.convert(traj, resolved=True, submission="sub")
    assert run is not None
    assert [s.kind for s in run.steps] == ["model", "tool"]
    assert run.started_at_us == 1_771_235_424_000_000
    assert run.steps[0].input_tokens == 2214
    assert run.costs[0] == Decimal("0.012900000")
    assert run.steps[1].error
    assert run.steps[1].args_hash == args_hash(ARGS_SALT, {"command": "ls"})


def test_swe_bench_responses_format() -> None:
    traj = {
        "instance_id": "x",
        "messages": [
            {
                "object": "response",
                "model": "gpt-5-mini",
                "created_at": 1771222810,
                "usage": {"input_tokens": 1359, "output_tokens": 117},
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "c1",
                        "arguments": '{"command":"ls -la"}',
                    }
                ],
                "extra": {"cost": 0.00057375, "timestamp": 1771222812.4},
            },
            {
                "type": "function_call_output",
                "call_id": "c1",
                "extra": {"returncode": 0, "timestamp": 1771222812.5},
            },
        ],
    }
    run = swebench.convert(traj, resolved=False, submission="sub")
    assert run is not None
    assert run.outcome == "failure"
    assert (run.steps[0].model, run.steps[0].output_tokens) == ("gpt-5-mini", 117)
    assert not run.steps[1].error


def _run(batch: str, i: int, start: int) -> CompactRun:
    step = StepRecord("tool", None, "t", None, 0, 0, 0.0, False, start + 7)
    return CompactRun(f"{batch}-{i}", batch, f"g{i}", start, "success", (step,), (Decimal(0),))


def test_batch_normalized_order_interleaves_and_keeps_offsets() -> None:
    fast = [_run("fast", i, 1000 + i) for i in range(4)]  # four runs within 4 µs
    slow = [_run("slow", i, 10**12 + i * 10**9) for i in range(2)]  # two runs far later
    ordered = sorted(batch_normalized_order(fast + slow), key=lambda r: (r.started_at_us, r.run_id))
    assert [r.run_id for r in ordered] == [
        "fast-0",
        "slow-0",
        "fast-1",
        "fast-2",
        "slow-1",
        "fast-3",
    ]
    assert all(r.steps[0].ts_us - r.started_at_us == 7 for r in ordered)
    assert all(r.started_at_us > ARRIVAL_EPOCH_US for r in ordered)


def test_shuffled_order_is_seeded_and_keeps_offsets() -> None:
    runs = [_run("a", i, 1000 + i) for i in range(50)] + [_run("b", i, 5000 + i) for i in range(50)]

    def order(seed: str) -> list[str]:
        shuffled = batch_normalized_order(runs, shuffle_seed=seed)
        assert all(r.steps[0].ts_us - r.started_at_us == 7 for r in shuffled)
        return [r.run_id for r in sorted(shuffled, key=lambda r: (r.started_at_us, r.run_id))]

    recorded = [
        r.run_id for r in sorted(batch_normalized_order(runs), key=lambda r: r.started_at_us)
    ]
    assert order("s") == order("s")
    assert order("s") != order("t")
    assert order("s") != recorded
    assert sorted(order("s")) == sorted(recorded)
    assert {rid[0] for rid in order("s")[:2]} == {"a", "b"}  # batches still interleave


@pytest.mark.parametrize(
    ("successes", "lost", "saved", "met"),
    [
        (100, 5, "0.100000000", True),
        (100, 10, "0.001000000", True),  # exactly 90% kept
        (1000, 101, "5.000000000", False),  # 89.9% kept
        (100, 1, "0.000000000", False),  # nothing saved
    ],
)
def test_s1_verdict(successes: int, lost: int, saved: str, met: bool) -> None:
    test = {
        "successes": successes,
        "successes_lost": lost,
        "saved": saved,
        "failed_spend_saved_share": 0.5,
    }
    summary = {"status": "validated", "reason": None, "test": test}
    assert s1_verdict(summary)["met"] is met


def test_s1_needs_a_policy() -> None:
    verdict = s1_verdict({"status": "insufficient_data", "reason": "too few", "test": None})
    assert verdict["met"] is False
