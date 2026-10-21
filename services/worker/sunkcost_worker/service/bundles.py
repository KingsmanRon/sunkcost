"""Bundle documents (bundle.v1): built from a calibration or an active bundle, validated, signed."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sunkcost._ids import uuid7
from sunkcost.core import FEATURE_SPEC, format_timestamp_us
from sunkcost_spec import models

BUNDLE_TTL_US = 7 * 86_400 * 1_000_000  # expires_at = issued_at + 7 days (§6.2)


def rate_str(value: Decimal) -> str:
    """A 0..1 rate with at most 4 decimals, as the wire writes it ("0.05", "0", "1")."""
    text = format(value.quantize(Decimal("0.0001")), "f").rstrip("0").rstrip(".")
    return text or "0"


def bundle_document(
    *,
    org_id: str,
    project_id: str,
    workload_id: str,
    workload: str,
    version: int,
    mode: str,
    holdout_rate: Decimal,
    model: dict[str, Any],
    decision: dict[str, Any],
    metrics: dict[str, Any],
    issued_at_us: int,
    bundle_id: str | None = None,
    check: bool = True,
) -> dict[str, Any]:
    """``check=False`` only for rejected bundles: they are stored for the record but never signed
    or served, and may lack the test metrics bundle.v1 requires."""
    doc = {
        "schema_version": "1",
        "bundle_id": bundle_id or uuid7(issued_at_us // 1000),
        "org_id": org_id,
        "project_id": project_id,
        "workload_id": workload_id,
        "workload": workload,
        "version": version,
        "feature_spec": FEATURE_SPEC,
        "mode": mode,
        "holdout_rate": rate_str(holdout_rate),
        "model": model,
        "decision": decision,
        "metrics": metrics,
        "issued_at": format_timestamp_us(issued_at_us),
        "expires_at": format_timestamp_us(issued_at_us + BUNDLE_TTL_US),
    }
    if check:
        validate(doc)
    return doc


def renewed(doc: dict[str, Any], *, issued_at_us: int, **changes: Any) -> dict[str, Any]:
    """A copy with fresh issued_at/expires_at (and any field changes), validated."""
    out = {**doc, **changes}
    out["issued_at"] = format_timestamp_us(issued_at_us)
    out["expires_at"] = format_timestamp_us(issued_at_us + BUNDLE_TTL_US)
    validate(out)
    return out


def validate(doc: dict[str, Any]) -> None:
    """Never sign a document the SDKs would refuse: check it against the bundle.v1 model."""
    models.Bundle.model_validate(doc)
