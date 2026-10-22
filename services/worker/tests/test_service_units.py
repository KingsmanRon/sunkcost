"""Worker service pieces that need no database (part of G2)."""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import httpx
import pytest

from sunkcost.bundle import load_trusted_keys, verify_response
from sunkcost.testing import TEST_PUBLIC_KEY_B64, TEST_SEED, TEST_TRUSTED_KEYS, make_bundle, sign
from sunkcost_worker.service import webhooks
from sunkcost_worker.service.bundles import bundle_document, rate_str, renewed
from sunkcost_worker.service.signing import Signer

SEED_B64 = base64.b64encode(TEST_SEED).decode()


def test_signer_matches_the_sdk_verifier() -> None:
    signer = Signer(SEED_B64, "rfc8032-test-1")
    assert signer.public_key_b64 == TEST_PUBLIC_KEY_B64
    doc = make_bundle("w", issued_at_us=1_790_000_000_000_000, ttl_s=10**9)
    assert signer.sign(doc) == sign(doc)["signature"]
    response = {
        "schema_version": "1",
        "bundle": doc,
        "key_id": "rfc8032-test-1",
        "signature": signer.sign(doc),
        "override": None,
    }
    bundle, _ = verify_response(
        response, load_trusted_keys(TEST_TRUSTED_KEYS), workload="w", now_us=1_790_000_000_000_001
    )
    assert bundle.version == 1


@pytest.mark.parametrize("seed", ["not base64!", base64.b64encode(b"short").decode()])
def test_signer_rejects_bad_keys(seed: str) -> None:
    with pytest.raises(ValueError, match="BUNDLE_SIGNING_KEY"):
        Signer(seed, "k")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.05", "0.05"),
        ("0.0500", "0.05"),
        ("0", "0"),
        ("1.0000", "1"),
        ("0.9500", "0.95"),
        ("0.0001", "0.0001"),
    ],
)
def test_rate_str(value: str, expected: str) -> None:
    assert rate_str(Decimal(value)) == expected


def _doc(**kw: object) -> dict[str, object]:
    base = make_bundle("w", issued_at_us=1_790_000_000_000_000)
    return bundle_document(
        org_id=base["org_id"],
        project_id=base["project_id"],
        workload_id=base["workload_id"],
        workload="w",
        version=3,
        mode="enforce",
        holdout_rate=Decimal("0.0500"),
        model=base["model"],
        decision=base["decision"],
        metrics={
            "n_train": 10,
            "n_calib_success": 5,
            "test_false_abort_rate": "0.01",
            "test_savings_share": "0.2",
            "auroc": "0.81",
        },
        issued_at_us=1_790_000_000_000_000,
        **kw,  # type: ignore[arg-type]
    )


def test_bundle_documents_are_valid_and_expire_after_seven_days() -> None:
    doc = _doc()
    assert doc["holdout_rate"] == "0.05"
    assert (doc["issued_at"], doc["expires_at"]) == (
        "2026-09-21T14:13:20.000000Z",
        "2026-09-28T14:13:20.000000Z",
    )
    later = renewed(doc, issued_at_us=1_790_086_400_000_000)
    assert later["version"] == 3
    assert later["expires_at"] == "2026-09-29T14:13:20.000000Z"


def test_invalid_documents_are_never_produced() -> None:
    with pytest.raises(ValueError, match="metrics"):
        _doc_with_float_metric()


def _doc_with_float_metric() -> None:
    base = make_bundle("w", issued_at_us=1_790_000_000_000_000)
    bundle_document(
        org_id=base["org_id"],
        project_id=base["project_id"],
        workload_id=base["workload_id"],
        workload="w",
        version=1,
        mode="enforce",
        holdout_rate=Decimal("0"),
        model=base["model"],
        decision=base["decision"],
        metrics={"auroc": 0.81},
        issued_at_us=1_790_000_000_000_000,
    )


def test_webhook_signature_round_trip_and_freshness() -> None:
    body = b'{"type":"workload.downgraded"}'
    header = webhooks.signature("whsec_x", 1_700_000_000, body)
    assert webhooks.verify("whsec_x", header, body, now=1_700_000_100)
    assert not webhooks.verify("whsec_y", header, body, now=1_700_000_100)
    assert not webhooks.verify("whsec_x", header, body + b" ", now=1_700_000_100)
    assert not webhooks.verify("whsec_x", header, body, now=1_700_001_000)  # replay outside 5 min
    assert not webhooks.verify("whsec_x", "garbage", body)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://10.1.2.3/x",
        "https://169.254.169.254/latest",
        "https://localhost/x",
        "https:///nohost",
    ],
)
def test_webhook_destinations_must_be_public_https(url: str) -> None:
    with pytest.raises(webhooks.WebhookError):
        webhooks.check_destination(url, allow_private=False)


def test_deliver_signs_and_raises_on_receiver_errors() -> None:
    seen: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204 if len(seen) == 1 else 500)

    event = {"type": "workload.downgraded", "workload_id": "w"}
    transport = httpx.MockTransport(receiver)
    status = webhooks.deliver(
        "https://hooks.test/x",
        "whsec_x",
        event,
        timeout_s=5,
        allow_private=True,
        transport=transport,
    )
    assert status == 204
    assert webhooks.verify("whsec_x", seen[0].headers["x-sunkcost-signature"], seen[0].content)
    assert json.loads(seen[0].content) == event
    with pytest.raises(webhooks.WebhookError, match="500"):
        webhooks.deliver(
            "https://hooks.test/x",
            "whsec_x",
            event,
            timeout_s=5,
            allow_private=True,
            transport=transport,
        )
    assert webhooks.MAX_ATTEMPTS == 5
    assert sum(webhooks.RETRY_DELAYS_S) == 3360  # about an hour across the five attempts
