"""Bundle verification: signature, pinning, expiry, rollback, and the unsigned override."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from sunkcost.bundle import BundleRejected, load_trusted_keys, verify_response
from sunkcost.core import parse_timestamp_us
from sunkcost.keys import DEFAULT_TRUSTED_KEYS
from sunkcost.testing import TEST_KEY_ID, TEST_PUBLIC_KEY_B64, TEST_TRUSTED_KEYS, make_bundle, sign
from sunkcost_spec.resources import FIXTURES_DIR

KEYS = load_trusted_keys(TEST_TRUSTED_KEYS)
WORKLOAD = "support.billing_dispute"


def now_us() -> int:
    return time.time_ns() // 1000


def response(**kwargs: Any) -> dict[str, Any]:
    override = kwargs.pop("override", None)
    kwargs.setdefault("issued_at_us", now_us())
    return sign(make_bundle(kwargs.pop("workload", WORKLOAD), **kwargs), override=override)


def reject(doc: Any, *, workload: str = WORKLOAD, at: int | None = None, floor: int = 0) -> str:
    with pytest.raises(BundleRejected) as info:
        verify_response(doc, KEYS, workload=workload, now_us=at or now_us(), min_version=floor)
    return info.value.reason


def test_spec_fixture_verifies_before_it_expires() -> None:
    doc = json.loads((FIXTURES_DIR / "valid" / "bundle-response.v1--signed.json").read_text())
    issued = parse_timestamp_us(doc["bundle"]["issued_at"])
    bundle, override = verify_response(doc, KEYS, workload=WORKLOAD, now_us=issued)
    assert (bundle.version, bundle.mode, bundle.threshold, bundle.min_steps) == (
        7,
        "enforce",
        0.842106,
        3,
    )
    assert override is None
    assert reject(doc) == "expired"  # its expires_at (2026-09-30) is in the past now


def test_valid_response_and_override() -> None:
    bundle, override = verify_response(
        response(override="shadow"), KEYS, workload=WORKLOAD, now_us=now_us()
    )
    assert override == "shadow"
    assert bundle.key_id == TEST_KEY_ID


def test_tampered_content_fails_the_signature() -> None:
    doc = response(threshold="0.9")
    doc["bundle"]["decision"]["threshold"] = "0.1"
    assert reject(doc) == "bad_signature"


def test_tampered_mode_fails_the_signature() -> None:
    doc = response(mode="shadow")
    doc["bundle"]["mode"] = "enforce"
    assert reject(doc) == "bad_signature"


def test_untrusted_key_id() -> None:
    doc = response()
    doc["key_id"] = "sc-2026-01"
    assert reject(doc) == "untrusted_key"


def test_other_key_with_same_id_fails() -> None:
    doc = sign(make_bundle(WORKLOAD, issued_at_us=now_us()), seed=bytes(32))
    assert reject(doc) == "bad_signature"


def test_wrong_workload() -> None:
    assert reject(response(workload="other")) == "wrong_workload"


def test_rollback_is_refused() -> None:
    assert reject(response(version=6), floor=7) == "rollback"
    verify_response(response(version=7), KEYS, workload=WORKLOAD, now_us=now_us(), min_version=7)


def test_expired() -> None:
    doc = response(ttl_s=10)
    assert reject(doc, at=now_us() + 11_000_000) == "expired"


@pytest.mark.parametrize("override", ["enforce", "", 1, True])
def test_override_can_only_reduce_enforcement(override: Any) -> None:
    doc = response()
    doc["override"] = override
    assert reject(doc) == "malformed"


def test_floats_in_signed_content_are_refused() -> None:
    doc = response()
    doc["bundle"]["metrics"]["auroc"] = 0.81  # a JSON float: canonical_json refuses it
    assert reject(doc) == "bad_signature"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(feature_spec="features.v2"),
        lambda b: b["model"].update(type="gbm_v1"),
        lambda b: b.update(holdout_rate="0.12345"),
        lambda b: b.update(version=0),
        lambda b: b["decision"].update(min_steps=-1),
        lambda b: b["model"].update(stds=["0"]),
    ],
)
def test_signed_but_unusable_bundles_are_refused(mutate: Any) -> None:
    bundle = make_bundle(WORKLOAD, issued_at_us=now_us())
    mutate(bundle)
    assert reject(sign(bundle)) in {"unsupported", "malformed"}


def test_test_key_is_never_trusted_by_default() -> None:
    assert TEST_KEY_ID not in DEFAULT_TRUSTED_KEYS
    assert TEST_PUBLIC_KEY_B64 not in DEFAULT_TRUSTED_KEYS.values()
    load_trusted_keys(DEFAULT_TRUSTED_KEYS)  # every default key parses
