"""G4 failure injection (ARCHITECTURE.md §10, §13): whatever the API or the SDK does, the agent
completes every run and every decision matches the failure table.

The API is a real HTTP server on 127.0.0.1 with switchable behaviour, so the SDK's own httpx
transport, retries and timeouts are what is under test.
"""

from __future__ import annotations

import gzip
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from sunkcost import Decision, Run, Sunkcost
from sunkcost.core import FeatureState
from sunkcost.testing import TEST_TRUSTED_KEYS, make_bundle, sign

pytestmark = pytest.mark.failure

WORKLOAD = "ci.fix_tests"
STEPS = 12


class FakeApi:
    """``behaviour`` is one of ``ok``, ``error`` (HTTP 500), ``slow`` (sleep ``delay_s`` first)."""

    def __init__(self) -> None:
        self.behaviour = "ok"
        self.delay_s = 10.0
        self.responses: dict[str, dict[str, Any]] = {}
        self.batches: list[dict[str, Any]] = []
        self.requests = 0
        self._lock = threading.Lock()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _gate(self) -> bool:
                with api._lock:
                    api.requests += 1
                if api.behaviour == "slow":
                    time.sleep(api.delay_s)
                if api.behaviour == "error":
                    self.send_response(500)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                return True

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not self._gate():
                    return
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                with api._lock:
                    api.batches.append(json.loads(body))
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                if not self._gate():
                    return
                workload = parse_qs(urlsplit(self.path).query).get("workload", [""])[0]
                doc = api.responses.get(workload)
                if doc is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(doc).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e for b in self.batches for e in b["events"]]

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# -- harness ----------------------------------------------------------------------------------


def now_us() -> int:
    return time.time_ns() // 1000


def signed(**kwargs: Any) -> dict[str, Any]:
    override = kwargs.pop("override", None)
    kwargs.setdefault("issued_at_us", now_us())
    kwargs.setdefault("threshold", "0.9")
    kwargs.setdefault("min_steps", 2)
    return sign(make_bundle(WORKLOAD, **kwargs), override=override)


@pytest.fixture
def api() -> Iterator[FakeApi]:
    server = FakeApi()
    yield server
    server.stop()


@pytest.fixture
def make_client(api: FakeApi) -> Iterator[Callable[..., Sunkcost]]:
    clients: list[Sunkcost] = []

    def factory(endpoint: str | None = None, **kwargs: Any) -> Sunkcost:
        kwargs.setdefault("trusted_keys", TEST_TRUSTED_KEYS)
        kwargs.setdefault("flush_interval_s", 0.05)
        kwargs.setdefault("workloads", [WORKLOAD])
        client = Sunkcost(
            api_key="sc_test_key", endpoint=endpoint or api.url, args_hash_salt="g4", **kwargs
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close(0.5)


def agent(client: Sunkcost, *, failing: bool = True) -> tuple[Run, list[Decision], float]:
    """A fake agent: up to STEPS tool steps, honouring ABORT. Returns the slowest SDK call (s)."""
    run = client.start_run(WORKLOAD)
    decisions: list[Decision] = []
    slowest = 0.0
    for _ in range(STEPS):
        t0 = time.perf_counter()
        d = run.check()
        slowest = max(slowest, time.perf_counter() - t0)
        decisions.append(d)
        if d.abort:
            break
        t0 = time.perf_counter()
        run.record_step("tool", tool_name="run_tests", tool_args={"k": 1}, error=failing)
        slowest = max(slowest, time.perf_counter() - t0)
    t0 = time.perf_counter()
    run.end("aborted" if decisions[-1].abort else "completed")
    slowest = max(slowest, time.perf_counter() - t0)
    return run, decisions, slowest


def wait_for(predicate: Callable[[], bool], timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


SLOW_CALL_S = 0.05  # no SDK call on the agent's path may take anywhere near a network round trip


# -- §10 rows ---------------------------------------------------------------------------------


def test_api_unreachable(make_client: Callable[..., Sunkcost]) -> None:
    client = make_client(endpoint=f"http://127.0.0.1:{free_port()}")
    client.refresh_bundles()
    _, decisions, slowest = agent(client)
    assert len(decisions) == STEPS  # no bundle yet: never aborts, the agent finishes
    assert {d.reason for d in decisions} == {"no_bundle"}
    assert slowest < SLOW_CALL_S
    assert client.bundles.fetch_errors >= 1
    t0 = time.monotonic()
    assert not client.close(1.0)  # nothing could be delivered...
    assert time.monotonic() - t0 < 1.5  # ...and closing is still bounded


def test_api_unreachable_keeps_cached_bundle_until_expiry(
    api: FakeApi, make_client: Callable[..., Sunkcost]
) -> None:
    api.responses[WORKLOAD] = signed(ttl_s=2)
    client = make_client()
    client.refresh_bundles()
    api.stop()  # the API goes away
    client.refresh_bundles()
    _, decisions, _ = agent(client)
    assert decisions[-1].reason == "above_threshold"  # cached bundle still enforced
    time.sleep(2.1)
    _, decisions, _ = agent(client)
    assert len(decisions) == STEPS
    assert {d.reason for d in decisions} == {"bundle_expired"}  # after expires_at: no aborts


def test_api_slow(api: FakeApi, make_client: Callable[..., Sunkcost]) -> None:
    api.behaviour, api.delay_s = "slow", 10.0
    client = make_client()
    _, decisions, slowest = agent(client)
    assert len(decisions) == STEPS
    assert slowest < SLOW_CALL_S
    t0 = time.monotonic()
    client.close()  # default bounded wait: 3 s
    assert time.monotonic() - t0 < 3.5


def test_api_errors_then_recovers(api: FakeApi, make_client: Callable[..., Sunkcost]) -> None:
    api.behaviour = "error"
    client = make_client()
    run, decisions, slowest = agent(client)
    assert len(decisions) == STEPS
    assert slowest < SLOW_CALL_S
    assert wait_for(lambda: client._buffer is not None and client._buffer.failed_sends >= 2)
    api.behaviour = "ok"
    assert client.flush(70)  # retried with backoff (capped at 60 s) and delivered
    sent = [e for e in api.events if e["run_id"] == run.run_id]
    assert sent[0]["type"] == "run_start"
    assert sent[-1]["type"] == "run_end"
    assert len(sent) == STEPS + 2


def test_bad_signature_keeps_previous_bundle(
    api: FakeApi, make_client: Callable[..., Sunkcost]
) -> None:
    client = make_client()
    bad = signed()
    bad["bundle"]["decision"]["threshold"] = "0.0"  # tampered after signing
    api.responses[WORKLOAD] = bad
    client.refresh_bundles()
    assert client.bundles.rejected >= 1  # the background refresher may have refused it too
    _, decisions, _ = agent(client)
    assert {d.reason for d in decisions} == {"no_bundle"}  # nothing usable yet

    api.responses[WORKLOAD] = signed(version=3)
    client.refresh_bundles()
    rejected = client.bundles.rejected
    api.responses[WORKLOAD] = {**signed(version=4), "signature": bad["signature"]}
    client.refresh_bundles()
    assert client.bundles.rejected > rejected
    _, decisions, _ = agent(client)
    assert decisions[-1].bundle_version == 3  # previous bundle kept


def test_expired_bundle_is_refused(api: FakeApi, make_client: Callable[..., Sunkcost]) -> None:
    api.responses[WORKLOAD] = signed(issued_at_us=now_us() - 8 * 86_400 * 1_000_000)
    client = make_client()
    client.refresh_bundles()
    _, decisions, _ = agent(client)
    assert len(decisions) == STEPS
    assert {d.reason for d in decisions} == {"no_bundle"}


def test_rollback_attempt_is_refused(api: FakeApi, make_client: Callable[..., Sunkcost]) -> None:
    api.responses[WORKLOAD] = signed(version=7, mode="shadow")
    client = make_client()
    client.refresh_bundles()
    api.responses[WORKLOAD] = signed(version=6, mode="enforce")  # older but validly signed
    client.refresh_bundles()
    _, decisions, _ = agent(client)
    assert decisions[-1].bundle_version == 7
    assert not any(d.abort for d in decisions)  # still the shadow bundle


def test_override_shadow_stops_aborts_at_next_refresh(
    api: FakeApi, make_client: Callable[..., Sunkcost]
) -> None:
    api.responses[WORKLOAD] = signed()
    client = make_client()
    client.refresh_bundles()
    assert agent(client)[1][-1].abort
    api.responses[WORKLOAD] = signed(override="shadow")
    client.refresh_bundles()
    _, decisions, _ = agent(client)
    assert len(decisions) == STEPS
    assert decisions[-1].reason == "override_shadow"


def test_buffer_overflow_drops_oldest_and_reports_it(
    api: FakeApi, make_client: Callable[..., Sunkcost]
) -> None:
    api.behaviour = "error"
    client = make_client(buffer_size=20, flush_batch_size=5)
    for _ in range(10):
        _, decisions, _ = agent(client, failing=False)
        assert len(decisions) == STEPS
    assert client.dropped_events > 0
    api.behaviour = "ok"
    assert client.flush(70)
    assert sum(b["dropped_events"] for b in api.batches) == client.dropped_events


def test_sdk_error_injection(
    api: FakeApi, make_client: Callable[..., Sunkcost], monkeypatch: pytest.MonkeyPatch
) -> None:
    api.responses[WORKLOAD] = signed()
    client = make_client()
    client.refresh_bundles()

    def boom(self: FeatureState, step: object) -> None:
        raise RuntimeError("injected")

    monkeypatch.setattr(FeatureState, "update", boom)
    _, decisions, _ = agent(client)
    assert len(decisions) == STEPS  # state never advances, so the check never aborts
    assert client.stats.sdk_errors == STEPS

    def vector_boom(self: FeatureState) -> list[float]:
        raise RuntimeError("injected")

    monkeypatch.setattr(FeatureState, "vector", vector_boom)
    _, decisions, _ = agent(client)
    assert len(decisions) == STEPS
    assert {(d.action, d.reason) for d in decisions} == {("CONTINUE", "sdk_error")}


CHILD = """
import sys, time
from sunkcost import Sunkcost
from sunkcost.testing import TEST_TRUSTED_KEYS
client = Sunkcost(api_key="k", endpoint=sys.argv[1], trusted_keys=TEST_TRUSTED_KEYS,
                  args_hash_salt="s", flush_interval_s=30)
for _ in range(int(sys.argv[2])):
    run = client.start_run("ci.fix_tests")
    run.check()
    run.record_step("tool", tool_name="t")
    run.end()
print("agent done", flush=True)
# exits without close(): the atexit hook must flush, within its 3 s bound
"""


def _child(url: str, runs: int) -> tuple[subprocess.CompletedProcess[str], float]:
    t0 = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", CHILD, url, str(runs)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONWARNINGS": "ignore"},
        check=False,
    )
    return proc, time.monotonic() - t0


def test_process_exit_flushes_pending_events(api: FakeApi) -> None:
    proc, _ = _child(api.url, 50)
    assert proc.returncode == 0, proc.stderr
    assert "agent done" in proc.stdout
    assert len(api.events) == 50 * 3


def test_process_exit_mid_flush_is_bounded(api: FakeApi) -> None:
    api.behaviour, api.delay_s = "slow", 20.0
    proc, elapsed = _child(api.url, 50)
    assert proc.returncode == 0, proc.stderr
    assert "agent done" in proc.stdout
    assert elapsed < 3.0 + 5.0  # 3 s flush bound plus interpreter start-up and teardown
