from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sunkcost import Sunkcost
from sunkcost.testing import TEST_TRUSTED_KEYS, MemoryTransport, events_of, make_bundle, sign
from sunkcost_cycles import RunAborted, bind_run, commit, guard, reserve

WORKLOAD = "cy.test"
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
ALWAYS_ABORT = {"threshold": "0.5", "min_steps": 0, "weights": {"step_index": "0"}}


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


class FakeCycles:
    def __init__(self) -> None:
        self.reserved: list[Any] = []
        self.committed: list[Any] = []

    def create_reservation(self, request: Any) -> str:
        self.reserved.append(request)
        return f"res-{len(self.reserved)}"

    def commit_reservation(self, reservation_id: str, request: Any) -> str:
        self.committed.append((reservation_id, request))
        return "COMMITTED"


def test_example_makes_no_reservation_for_the_aborted_call() -> None:
    example = load_example("cycles_agent")
    reservations = example.main()
    assert reservations == 6  # six steps ran; the seventh was aborted before reserving


def test_reserve_and_commit_record_the_actual_cost() -> None:
    transport = MemoryTransport()
    sl = client(transport)
    cy = FakeCycles()
    with bind_run(sl.start_run(WORKLOAD)) as run:
        rid = reserve(cy, {"action": {"kind": "llm.completion", "name": "m"}})
        commit(
            cy,
            rid,
            {"actual": {"unit": "USD_MICROCENTS", "amount": 250_000}},
            kind="model",
            name="m",
        )
        run.end()
    assert sl.flush(3)
    (step,) = events_of(transport, run.run_id, ["step"])
    assert step["cost_usd"] == "0.0025"  # 250,000 microcents
    assert run.total_cost_usd == Decimal("0.0025")
    sl.close()


def test_abort_means_no_reservation() -> None:
    sl = client(MemoryTransport(), **ALWAYS_ABORT)
    cy = FakeCycles()
    with bind_run(sl.start_run(WORKLOAD)), pytest.raises(RunAborted):
        reserve(cy, {})
    assert cy.reserved == []
    with bind_run(sl.start_run(WORKLOAD)):
        assert reserve(cy, {}, raise_on_abort=False) is None
    assert cy.reserved == []
    sl.close()


def test_guard_sync_async_and_passthrough() -> None:
    sl = client(MemoryTransport(), **ALWAYS_ABORT)
    calls: list[str] = []

    @guard(kind="tool", name="t")
    def work() -> str:
        calls.append("sync")
        return "ok"

    @guard(kind="model", name="m")
    async def awork() -> str:
        calls.append("async")
        return "ok"

    assert work() == "ok"  # no bound run: passes straight through
    with bind_run(sl.start_run(WORKLOAD)):
        with pytest.raises(RunAborted):
            work()
        with pytest.raises(RunAborted):
            asyncio.run(awork())
    assert calls == ["sync"]
    sl.close()
