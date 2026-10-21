"""The committed vectors are reproducible, well-formed and internally consistent."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any, get_args

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sunkcost_spec import models
from sunkcost_spec.resources import FIXTURES_DIR, VECTORS_DIR, load_json

FEATURE_VECTORS = sorted((VECTORS_DIR / "features.v1").glob("*.json"))
CANONICAL_VECTORS = sorted(
    p for p in (VECTORS_DIR / "canonical-json").glob("*.json") if p.name != "must-reject.json"
)


def test_vectors_are_up_to_date(gen: ModuleType) -> None:
    assert gen.main(["--check"]) == 0, "run: uv run python packages/spec/tools/gen_vectors.py"


def test_rfc8032_test_1(gen: ModuleType) -> None:
    # RFC 8032 section 7.1, TEST 1: the empty message.
    expected = (
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e3970"
        "1cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert base64.b64decode(gen.sign_b64(b"")).hex() == expected


@pytest.mark.parametrize("path", FEATURE_VECTORS, ids=lambda p: p.stem)
def test_feature_vector_shape(path: Path) -> None:
    doc = load_json(path)
    names = doc["feature_names"]
    assert names == list(get_args(models.FeatureName))
    assert [e["k"] for e in doc["expected"]] == list(range(len(doc["steps"]) + 1))
    for e in doc["expected"]:
        assert len(e["features"]) == len(names)
        for value in [*e["features"], e["score"]]:
            assert repr(float(value)) == value  # shortest round-trip float64 strings
    models.LogisticV1.model_validate(doc["model"])


@pytest.mark.parametrize("path", FEATURE_VECTORS, ids=lambda p: p.stem)
def test_feature_vector_steps_are_valid_wire_events(path: Path) -> None:
    doc = load_json(path)
    if not doc["steps"]:
        pytest.skip("no steps")
    batch = {
        "schema_version": "1",
        "sent_at": "2026-09-23T10:00:00Z",
        "sdk": {"name": "vectors", "version": "1"},
        "dropped_events": 0,
        "events": doc["steps"],
    }
    models.IngestBatch.model_validate_json(json.dumps(batch))


@pytest.mark.parametrize("path", CANONICAL_VECTORS, ids=lambda p: p.stem)
def test_canonical_vector_signature_verifies(path: Path) -> None:
    doc = load_json(path)
    data = bytes.fromhex(doc["canonical_hex"])
    assert json.loads(data.decode("utf-8")) == doc["input"]
    sig = doc["signature"]
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["public_key_b64"]))
    key.verify(base64.b64decode(sig["signature_b64"]), data)  # raises InvalidSignature


def test_must_reject_cases_are_rejected(gen: ModuleType) -> None:
    doc = load_json(VECTORS_DIR / "canonical-json" / "must-reject.json")
    assert doc["cases"]
    for case in doc["cases"]:
        with pytest.raises(gen.CanonicalError):
            gen.canonical(json.loads(case["json_text"]))


def test_signed_fixtures_verify(gen: ModuleType) -> None:
    for name in ("bundle-response.v1--signed.json", "bundle-response.v1--override-shadow.json"):
        doc: dict[str, Any] = load_json(FIXTURES_DIR / "valid" / name)
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(gen.TEST_KEY_PUBLIC_HEX))
        key.verify(base64.b64decode(doc["signature"]), gen.canonical(doc["bundle"]))


def test_holdout_vectors() -> None:
    doc = load_json(VECTORS_DIR / "holdout.v1" / "buckets.json")
    rates = doc["rates"]
    buckets = set()
    for case in doc["cases"]:
        digest = hashlib.sha256(case["run_id"].encode("ascii")).hexdigest()
        assert digest[:8] == case["sha256_hex_prefix"]
        assert int(digest[:8], 16) % 10_000 == case["bucket"]
        thresholds = [round(float(r) * 10_000) for r in rates]
        assert case["holdout"] == [case["bucket"] < t for t in thresholds]
        buckets.add(case["bucket"])
    assert {0, 1, 499, 500, 4999, 5000, 9998, 9999} <= buckets


def test_args_hash_vectors(gen: ModuleType) -> None:
    doc = load_json(VECTORS_DIR / "args-hash.v1" / "cases.json")
    by_name = {c["name"]: c for c in doc["cases"]}
    assert by_name["object"]["args_hash"] == by_name["key-order-irrelevant"]["args_hash"]
    assert by_name["object"]["args_hash"] != by_name["same-object-other-salt"]["args_hash"]
    for case in doc["cases"]:
        assert gen.args_hash(case["salt"], case["args"]) == case["args_hash"]
