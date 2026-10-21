from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException, tool

from sunkcost import Sunkcost
from sunkcost.core import args_hash
from sunkcost.testing import TEST_TRUSTED_KEYS, MemoryTransport, events_of, make_bundle, sign
from sunkcost_langgraph import RunAborted, SunkcostCallbackHandler, is_abort

WORKLOAD = "lg.test"
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


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


@tool
def lookup(order_id: str) -> str:
    """Look up an order."""
    if order_id == "bad":
        raise ToolException("timeout")
    return "ok"


def test_records_model_and_tool_steps_with_usage() -> None:
    transport = MemoryTransport()
    sl = client(transport)
    run = sl.start_run(WORKLOAD)
    handler = SunkcostCallbackHandler(run)
    reply = AIMessage(
        content="hi", usage_metadata={"input_tokens": 321, "output_tokens": 12, "total_tokens": 333}
    )
    FakeMessagesListChatModel(responses=[reply]).invoke("hello", config={"callbacks": [handler]})
    lookup.invoke({"order_id": "A-1"}, config={"callbacks": [handler]})
    with pytest.raises(ToolException):
        lookup.invoke({"order_id": "bad"}, config={"callbacks": [handler]})
    run.end()
    assert sl.flush(3)
    model, ok, failed = events_of(transport, run.run_id, ["step"])
    assert (model["kind"], model["input_tokens"], model["output_tokens"]) == ("model", 321, 12)
    assert model["decision"]["reason"] == "no_bundle"
    assert (ok["tool_name"], ok["error"]) == ("lookup", False)
    assert ok["args_hash"] == args_hash("salt", {"order_id": "A-1"})
    assert failed["error"] is True
    sl.close()


def test_abort_raises_before_the_call_and_records_nothing_for_it() -> None:
    transport = MemoryTransport()
    sl = client(transport, threshold="0.5", min_steps=0, weights={"step_index": "0"})  # always 0.5
    run = sl.start_run(WORKLOAD)
    model = FakeMessagesListChatModel(responses=[AIMessage(content="never")])
    with pytest.raises(RunAborted) as info:
        model.invoke("hello", config={"callbacks": [SunkcostCallbackHandler(run)]})
    assert is_abort(info.value)
    assert run.steps == 0
    sl.close()


def test_raise_on_abort_false_only_records() -> None:
    sl = client(MemoryTransport(), threshold="0.5", min_steps=0, weights={"step_index": "0"})
    run = sl.start_run(WORKLOAD)
    handler = SunkcostCallbackHandler(run, raise_on_abort=False)
    FakeMessagesListChatModel(responses=[AIMessage(content="x")]).invoke(
        "hi", config={"callbacks": [handler]}
    )
    assert handler.aborted is not None
    assert run.steps == 1
    sl.close()


def test_example_routes_the_abort_through_the_node_error_handler() -> None:
    status = load_example("langgraph_agent").main()
    assert status == "aborted by sunkcost in node 'tools'"
