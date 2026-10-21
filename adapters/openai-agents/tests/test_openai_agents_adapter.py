from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from agents import Agent, function_tool, set_tracing_disabled
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.usage import Usage

from sunkcost import Sunkcost
from sunkcost.core import args_hash
from sunkcost.testing import TEST_TRUSTED_KEYS, MemoryTransport, events_of, make_bundle, sign
from sunkcost_openai_agents import RunAborted, run_agent

WORKLOAD = "oa.test"
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
USAGE = Usage(requests=1, input_tokens=700, output_tokens=20, total_tokens=720)

set_tracing_disabled(True)


def load_example(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # type hints (TypedDict state) resolve through sys.modules
    spec.loader.exec_module(module)
    return module


def client(transport: MemoryTransport, **bundle: Any) -> Sunkcost:
    sl = Sunkcost(
        transport=transport,
        trusted_keys=TEST_TRUSTED_KEYS,
        args_hash_salt="salt",
        flush_interval_s=0.05,
    )
    if bundle:
        bundle.setdefault("issued_at_us", time.time_ns() // 1000)
        sl.bundles.install(WORKLOAD, sign(make_bundle(WORKLOAD, **bundle)))
    return sl


@function_tool
def lookup(order_id: str) -> str:
    """Look up an order."""
    return "shipped"


def agent(steps: list[Any]) -> tuple[Agent[Any], ScriptedModel]:
    model = ScriptedModel(steps, default_usage=USAGE)
    return Agent(name="a", instructions="x", model=model, tools=[lookup]), model


def test_records_model_and_tool_steps() -> None:
    transport = MemoryTransport()
    sl = client(transport)
    a, _ = agent(
        [[function_call("lookup", {"order_id": "A-1"}, call_id="c1")], [assistant_message("done")]]
    )
    run = sl.start_run(WORKLOAD)
    asyncio.run(run_agent(a, "where is A-1?", run=run))
    run.end()
    assert sl.flush(3)
    m1, tool, m2 = events_of(transport, run.run_id, ["step"])
    assert (m1["kind"], m1["input_tokens"], m1["output_tokens"]) == ("model", 700, 20)
    assert (tool["kind"], tool["tool_name"]) == ("tool", "lookup")
    assert tool["args_hash"] == args_hash("salt", {"order_id": "A-1"})
    assert m2["kind"] == "model"
    sl.close()


def test_abort_before_a_model_call_is_unwrapped() -> None:
    sl = client(MemoryTransport(), threshold="0.5", min_steps=0, weights={"step_index": "0"})
    a, model = agent([[assistant_message("never")]])
    run = sl.start_run(WORKLOAD)
    with pytest.raises(RunAborted):
        asyncio.run(run_agent(a, "hi", run=run))
    assert len(model.calls) == 0  # the model was never called
    sl.close()


def test_abort_before_a_tool_call_surfaces_as_run_aborted() -> None:
    # Abort once one step is recorded: the model call runs, the tool call does not.
    sl = client(MemoryTransport(), threshold="0.5", min_steps=1, weights={"step_index": "0"})
    a, model = agent(
        [[function_call("lookup", {"order_id": "A-1"}, call_id="c1")], [assistant_message("x")]]
    )
    run = sl.start_run(WORKLOAD)
    with pytest.raises(RunAborted) as info:
        asyncio.run(run_agent(a, "hi", run=run))
    assert info.value.__cause__ is not None  # the SDK wrapped it in UserError
    assert len(model.calls) == 1
    assert run.steps == 1
    sl.close()


def test_example_aborts() -> None:
    assert load_example("openai_agents_agent").main().startswith("aborted: above_threshold")
