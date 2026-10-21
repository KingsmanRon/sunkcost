"""Shared fixtures: the reference vector generator (core) and SDK clients on an in-memory API."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest

from sunkcost import Sunkcost
from sunkcost.testing import TEST_TRUSTED_KEYS, MemoryTransport, make_bundle, sign
from sunkcost_spec.resources import SPEC_ROOT
from sunkcost_spec.validation import model_errors, schema_errors

WORKLOAD = "support.billing_dispute"


def now_us() -> int:
    return time.time_ns() // 1000


def signed(**kwargs: Any) -> dict[str, Any]:
    override = kwargs.pop("override", None)
    kwargs.setdefault("issued_at_us", now_us())
    return sign(make_bundle(kwargs.pop("workload", WORKLOAD), **kwargs), override=override)


def assert_valid_batches(transport: MemoryTransport) -> None:
    """Every batch the SDK sent passes both the JSON Schema and the Pydantic model."""
    assert transport.batches, "nothing was sent"
    for batch in transport.batches:
        assert schema_errors("ingest-batch.v1", batch) == []
        assert model_errors("ingest-batch.v1", json.dumps(batch)) == []


@pytest.fixture(name="signed")
def signed_fixture() -> Callable[..., dict[str, Any]]:
    return signed


@pytest.fixture(name="assert_valid_batches")
def assert_valid_batches_fixture() -> Callable[[MemoryTransport], None]:
    return assert_valid_batches


@pytest.fixture
def transport() -> MemoryTransport:
    return MemoryTransport()


@pytest.fixture
def make_client(transport: MemoryTransport) -> Iterator[Callable[..., Sunkcost]]:
    clients: list[Sunkcost] = []

    def factory(**kwargs: Any) -> Sunkcost:
        kwargs.setdefault("transport", transport)
        kwargs.setdefault("trusted_keys", TEST_TRUSTED_KEYS)
        kwargs.setdefault("args_hash_salt", "test-salt")
        kwargs.setdefault("flush_interval_s", 0.05)
        kwargs.setdefault("workloads", [WORKLOAD])
        client = Sunkcost(**kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close(1.0)


@pytest.fixture(scope="session")
def reference() -> ModuleType:
    """The independent reference implementation that generated the golden vectors."""
    name = "sunkcost_spec_gen_vectors"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SPEC_ROOT / "tools" / "gen_vectors.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
