"""Event buffer (overflow, retries, rejects) and bundle manager (ETag, 404, disk cache)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sunkcost.buffer import EventBuffer
from sunkcost.bundle import load_trusted_keys
from sunkcost.bundles import BundleManager
from sunkcost.testing import TEST_TRUSTED_KEYS, MemoryTransport
from sunkcost.transport import FetchResult, SendResult, classify

WORKLOAD = "support.billing_dispute"
KEYS = load_trusted_keys(TEST_TRUSTED_KEYS)
Signed = Callable[..., dict[str, Any]]


def event(i: int) -> dict[str, Any]:
    return {
        "type": "outcome",
        "run_id": f"r{i}",
        "outcome": "success",
        "source": "sdk",
        "reported_at": "x",
    }


def test_classify() -> None:
    assert [classify(s) for s in (200, 202, 400, 401, 413, 408, 429, 500, 503)] == [
        "ok",
        "ok",
        "drop",
        "drop",
        "drop",
        "retry",
        "retry",
        "retry",
        "retry",
    ]


def test_overflow_drops_oldest_and_reports_the_count() -> None:
    transport = MemoryTransport()
    buf = EventBuffer(transport, capacity=10, batch_size=100, interval_s=60)
    for i in range(25):
        buf.put(event(i))
    buf.start()
    assert buf.flush(3)
    (batch,) = transport.batches
    assert [e["run_id"] for e in batch["events"]] == [f"r{i}" for i in range(15, 25)]
    assert batch["dropped_events"] == 15
    buf.put(event(99))
    assert buf.flush(3)
    assert transport.batches[-1]["dropped_events"] == 0  # reset after an accepted batch
    buf.close(1)


def test_retries_then_delivers_in_order() -> None:
    transport = MemoryTransport()
    transport.send_results = [SendResult("retry"), SendResult("retry", retry_after_s=0.0)]
    buf = EventBuffer(transport, capacity=100, batch_size=2, interval_s=0.01)
    buf.start()
    for i in range(5):
        buf.put(event(i))
    deadline = time.monotonic() + 10
    while buf.queued and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [e["run_id"] for e in transport.events] == [f"r{i}" for i in range(5)]
    assert buf.failed_sends == 2
    buf.close(1)


def test_permanent_reject_drops_the_batch_and_counts_it() -> None:
    transport = MemoryTransport()
    transport.send_results = [SendResult("drop", http_status=400)]
    buf = EventBuffer(transport, capacity=100, batch_size=3, interval_s=0.01)
    for i in range(4):
        buf.put(event(i))
    buf.start()
    assert buf.flush(3)
    (batch,) = transport.batches
    assert [e["run_id"] for e in batch["events"]] == ["r3"]
    assert batch["dropped_events"] == 3
    buf.close(1)


def test_batches_respect_the_size_limit() -> None:
    transport = MemoryTransport()
    buf = EventBuffer(transport, capacity=5000, batch_size=500, interval_s=60)
    for i in range(1200):
        buf.put(event(i))
    buf.start()
    assert buf.flush(5)
    assert [len(b["events"]) for b in transport.batches] == [500, 500, 200]
    buf.close(1)


# -- bundle manager -------------------------------------------------------------------------------


def test_etag_and_404(signed: Signed) -> None:
    transport = MemoryTransport()
    transport.serve(WORKLOAD, signed(version=3))
    manager = BundleManager(transport, KEYS)
    assert manager.refresh(WORKLOAD)
    assert not manager.refresh(WORKLOAD)  # 304
    transport.serve(WORKLOAD, None)
    assert manager.refresh(WORKLOAD)  # 404 clears it: can only reduce enforcement
    assert manager.get(WORKLOAD) == (None, None)


def test_rejected_bundle_keeps_the_previous_one(signed: Signed) -> None:
    transport = MemoryTransport()
    transport.serve(WORKLOAD, signed(version=5))
    manager = BundleManager(transport, KEYS)
    manager.refresh(WORKLOAD)
    bad = signed(version=6)
    bad["signature"] = signed(version=7)["signature"]
    transport.serve(WORKLOAD, bad)
    assert not manager.refresh(WORKLOAD)
    assert manager.rejected == 1
    assert manager.get(WORKLOAD)[0].version == 5  # type: ignore[union-attr]
    transport.serve(WORKLOAD, signed(version=4))  # rollback
    manager.refresh(WORKLOAD)
    assert manager.rejected == 2
    assert manager.get(WORKLOAD)[0].version == 5  # type: ignore[union-attr]


def test_fetch_errors_keep_the_cached_bundle(signed: Signed) -> None:
    class Flaky(MemoryTransport):
        down = False

        def fetch_bundle(self, workload: str, etag: str | None) -> FetchResult:
            if self.down:
                raise OSError("unreachable")
            return super().fetch_bundle(workload, etag)

    transport = Flaky()
    transport.serve(WORKLOAD, signed())
    manager = BundleManager(transport, KEYS)
    manager.refresh(WORKLOAD)
    transport.down = True
    manager.refresh(WORKLOAD)
    assert manager.get(WORKLOAD)[0] is not None
    assert manager.fetch_errors == 1


def test_disk_cache_survives_restart_and_keeps_the_version_floor(
    tmp_path: Path, signed: Signed
) -> None:
    transport = MemoryTransport()
    transport.serve(WORKLOAD, signed(version=9))
    BundleManager(transport, KEYS, cache_dir=tmp_path).refresh(WORKLOAD)

    offline = BundleManager(None, KEYS, cache_dir=tmp_path)
    offline.register(WORKLOAD)
    assert offline.get(WORKLOAD)[0].version == 9  # type: ignore[union-attr]

    path = next(tmp_path.glob("bundle-*.json"))
    doc = json.loads(path.read_text())
    older = signed(version=8)
    path.write_text(json.dumps({**older, "_sunkcost_floor": doc["_sunkcost_floor"]}))
    restarted = BundleManager(None, KEYS, cache_dir=tmp_path)
    restarted.register(WORKLOAD)
    assert restarted.get(WORKLOAD) == (None, None)  # version 8 < floor 9

    path.write_text("{ not json")
    broken = BundleManager(None, KEYS, cache_dir=tmp_path)
    broken.register(WORKLOAD)
    assert broken.get(WORKLOAD) == (None, None)
