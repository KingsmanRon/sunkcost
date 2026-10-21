"""Signed policy bundles: verification and parsing (§4 C2 bundle manager, §6.2, §9).

A bundle response is accepted only if its Ed25519 signature over ``canonical_json(bundle)``
verifies under a pinned trusted key, it names the expected workload, it has not expired, and its
version is not lower than the highest version already accepted for that workload. The unsigned
``override`` may only be ``null`` or ``"shadow"``, so it can only reduce enforcement.
"""

from __future__ import annotations

import base64
import binascii
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sunkcost._ids import UUID
from sunkcost.core import (
    FEATURE_SPEC,
    CanonicalJSONError,
    LogisticModel,
    ModelError,
    canonical_json,
    holdout_threshold,
    parse_timestamp_us,
)

MODES = ("shadow", "enforce")


class BundleRejected(Exception):  # noqa: N818 - a verdict, not an error
    """A bundle response failed verification; ``reason`` is a short machine-readable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Bundle:
    """A verified bundle, parsed for the decision engine."""

    bundle_id: str
    version: int
    workload: str
    mode: str
    holdout_rate: str
    model: LogisticModel
    threshold: float
    min_steps: int
    issued_at_us: int
    expires_at_us: int
    key_id: str


def load_trusted_keys(keys: Mapping[str, str]) -> dict[str, Ed25519PublicKey]:
    """Parse ``{key_id: base64 raw 32-byte Ed25519 public key}``."""
    out: dict[str, Ed25519PublicKey] = {}
    for key_id, b64 in keys.items():
        raw = base64.b64decode(b64, validate=True)
        if len(raw) != 32:
            raise ValueError(f"trusted key {key_id!r} is not a 32-byte Ed25519 public key")
        out[key_id] = Ed25519PublicKey.from_public_bytes(raw)
    return out


def _float(value: Any, what: str) -> float:
    if not isinstance(value, str):
        raise BundleRejected("malformed", f"{what} must be a decimal string")
    try:
        x = float(value)
    except ValueError:
        raise BundleRejected("malformed", f"{what} is not a number") from None
    if not math.isfinite(x):
        raise BundleRejected("malformed", f"{what} is not finite")
    return x


def _int(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BundleRejected("malformed", f"{what} must be an integer in [{low}, {high}]")
    return value


def verify_response(
    doc: Any,
    trusted_keys: Mapping[str, Ed25519PublicKey],
    *,
    workload: str,
    now_us: int,
    min_version: int = 0,
) -> tuple[Bundle, str | None]:
    """Verify a ``bundle-response.v1`` document; returns the bundle and the override."""
    if not isinstance(doc, dict) or doc.get("schema_version") != "1":
        raise BundleRejected("malformed", "not a bundle-response.v1 document")
    override = doc.get("override")
    if override not in (None, "shadow"):
        raise BundleRejected("malformed", "override must be null or 'shadow'")
    key_id, signature, bundle = doc.get("key_id"), doc.get("signature"), doc.get("bundle")
    if (
        not isinstance(key_id, str)
        or not isinstance(signature, str)
        or not isinstance(bundle, dict)
    ):
        raise BundleRejected("malformed", "key_id, signature and bundle are required")
    key = trusted_keys.get(key_id)
    if key is None:
        raise BundleRejected("untrusted_key", key_id)
    try:
        sig = base64.b64decode(signature, validate=True)
        message = canonical_json(bundle)
    except (binascii.Error, ValueError, CanonicalJSONError) as exc:
        raise BundleRejected("bad_signature", str(exc)) from None
    try:
        key.verify(sig, message)
    except InvalidSignature:
        raise BundleRejected("bad_signature") from None

    # Signed content from here on; still validated, since a trusted key could sign a bad bundle.
    if bundle.get("schema_version") != "1" or bundle.get("feature_spec") != FEATURE_SPEC:
        raise BundleRejected("unsupported", "schema_version or feature_spec")
    if bundle.get("workload") != workload:
        raise BundleRejected("wrong_workload", str(bundle.get("workload")))
    bundle_id = bundle.get("bundle_id")
    if not isinstance(bundle_id, str) or UUID.fullmatch(bundle_id) is None:
        raise BundleRejected("malformed", "bundle_id")
    version = _int(bundle.get("version"), "version", 1, 2_147_483_647)
    if version < min_version:
        raise BundleRejected("rollback", f"version {version} < {min_version}")
    mode = bundle.get("mode")
    if mode not in MODES:
        raise BundleRejected("malformed", "mode")
    holdout_rate = bundle.get("holdout_rate")
    try:
        holdout_threshold(holdout_rate)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise BundleRejected("malformed", "holdout_rate") from None
    decision = bundle.get("decision")
    if not isinstance(decision, dict):
        raise BundleRejected("malformed", "decision")
    try:
        model = LogisticModel.from_bundle(bundle.get("model") or {})
        issued_at_us = parse_timestamp_us(bundle["issued_at"])
        expires_at_us = parse_timestamp_us(bundle["expires_at"])
    except (ModelError, KeyError, TypeError, ValueError) as exc:
        raise BundleRejected("malformed", str(exc)) from None
    if expires_at_us <= now_us:
        raise BundleRejected("expired", bundle["expires_at"])
    parsed = Bundle(
        bundle_id=bundle_id,
        version=version,
        workload=workload,
        mode=mode,
        holdout_rate=holdout_rate,  # type: ignore[arg-type]
        model=model,
        threshold=_float(decision.get("threshold"), "threshold"),
        min_steps=_int(decision.get("min_steps"), "min_steps", 0, 1_000_000),
        issued_at_us=issued_at_us,
        expires_at_us=expires_at_us,
        key_id=key_id,
    )
    return parsed, override
