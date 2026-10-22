"""Helpers for tests and offline examples: an in-memory API and a bundle signer.

The signer uses the **public** RFC 8032 §7.1 TEST 1 key (``key_id`` ``rfc8032-test-1``). It is not
trusted by default; pass ``TEST_TRUSTED_KEYS`` explicitly. Never use it for real bundles.
"""

from __future__ import annotations

import base64
import copy
import json
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sunkcost._ids import uuid7
from sunkcost.core import FEATURE_NAMES, canonical_json, format_timestamp_us
from sunkcost.transport import FetchResult, SendResult

if TYPE_CHECKING:
    from sunkcost.client import Sunkcost

TEST_KEY_ID = "rfc8032-test-1"
TEST_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
TEST_PUBLIC_KEY_B64 = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
TEST_TRUSTED_KEYS = {TEST_KEY_ID: TEST_PUBLIC_KEY_B64}


def make_bundle(
    workload: str,
    *,
    version: int = 1,
    mode: str = "enforce",
    threshold: str = "0.5",
    min_steps: int = 3,
    holdout_rate: str = "0",
    intercept: str = "0.0",
    weights: Mapping[str, str] | None = None,
    issued_at_us: int,
    ttl_s: int = 7 * 86_400,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    """A ``bundle.v1`` document. ``weights`` maps feature names to decimal-string weights."""
    weights = dict(weights or {"error_streak": "2.0"})
    names = [n for n in FEATURE_NAMES if n in weights]
    return {
        "schema_version": "1",
        "bundle_id": bundle_id or uuid7(issued_at_us // 1000),
        "org_id": "01a03336-8088-7f77-9846-a07c2bc1bf65",
        "project_id": "01a03336-8470-7088-a957-b18d3cd2c076",
        "workload_id": "01a03336-7ca0-7e66-8735-9f6b1ab0ae54",
        "workload": workload,
        "version": version,
        "feature_spec": "features.v1",
        "mode": mode,
        "holdout_rate": holdout_rate,
        "model": {
            "type": "logistic_v1",
            "intercept": intercept,
            "features": names,
            "weights": [weights[n] for n in names],
            "means": ["0.0"] * len(names),
            "stds": ["1.0"] * len(names),
        },
        "decision": {
            "threshold": threshold,
            "min_steps": min_steps,
            "target_recall": "0.95",
            "delta": "0.05",
        },
        "metrics": {
            "n_train": 0,
            "n_calib_success": 0,
            "test_false_abort_rate": "0.0",
            "test_savings_share": "0.0",
            "auroc": "0.5",
        },
        "issued_at": format_timestamp_us(issued_at_us),
        "expires_at": format_timestamp_us(issued_at_us + ttl_s * 1_000_000),
    }


def sign(
    bundle: Mapping[str, Any], *, override: str | None = None, seed: bytes = TEST_SEED
) -> dict[str, Any]:
    """Wrap a bundle in a signed ``bundle-response.v1``."""
    key = Ed25519PrivateKey.from_private_bytes(seed)
    signature = key.sign(canonical_json(dict(bundle)))
    return {
        "schema_version": "1",
        "bundle": copy.deepcopy(dict(bundle)),
        "key_id": TEST_KEY_ID,
        "signature": base64.b64encode(signature).decode("ascii"),
        "override": override,
    }


class MemoryTransport:
    """An in-process stand-in for the API: records batches and serves bundle responses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.batches: list[dict[str, Any]] = []
        self.responses: dict[str, dict[str, Any]] = {}
        self.fetches = 0
        self.send_results: list[SendResult] = []  # scripted results, consumed in order

    def serve(self, workload: str, response: dict[str, Any] | None) -> None:
        with self._lock:
            if response is None:
                self.responses.pop(workload, None)
            else:
                self.responses[workload] = response

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e for b in self.batches for e in b["events"]]

    def send_batch(self, body: bytes) -> SendResult:
        with self._lock:
            if self.send_results:
                result = self.send_results.pop(0)
                if result.status != "ok":
                    return result
            self.batches.append(json.loads(body))
            return SendResult("ok", http_status=202)

    def fetch_bundle(self, workload: str, etag: str | None) -> FetchResult:
        with self._lock:
            self.fetches += 1
            response = self.responses.get(workload)
        if response is None:
            return FetchResult(404)
        body = json.dumps(response).encode("utf-8")
        tag = f'"{hash(body) & 0xFFFFFFFF:x}"'
        if etag == tag:
            return FetchResult(304, etag=tag)
        return FetchResult(200, body, tag)

    def close(self) -> None:
        pass


def events_of(
    transport: MemoryTransport, run_id: str, types: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    return [
        e
        for e in transport.events
        if e["run_id"] == run_id and (types is None or e["type"] in types)
    ]


def demo_client(workload: str, **bundle: Any) -> Sunkcost:
    """An offline ``Sunkcost`` trusting only the test key, with a freshly signed bundle installed.

    For examples and tests that run without an API: decisions are real, events are discarded.
    ``bundle`` is passed to ``make_bundle`` (``threshold``, ``weights``, ``min_steps``, …).
    """
    import time

    from sunkcost.client import Sunkcost

    client = Sunkcost(trusted_keys=TEST_TRUSTED_KEYS, args_hash_salt="demo-salt")
    bundle.setdefault("issued_at_us", time.time_ns() // 1000)
    client.bundles.install(workload, sign(make_bundle(workload, **bundle)))
    return client
