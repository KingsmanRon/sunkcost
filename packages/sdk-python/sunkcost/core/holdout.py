"""Deterministic holdout assignment, identical in both SDKs (ARCHITECTURE.md §4 C2, ADR-0002)."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal

BUCKETS = 10_000
_RATE = re.compile(r"0(\.[0-9]{1,4})?|1(\.0{1,4})?")


def holdout_bucket(run_id: str) -> int:
    """First 8 hex digits of SHA-256(lower-case run_id), as a uint32, modulo 10,000."""
    digest = hashlib.sha256(run_id.lower().encode("ascii")).hexdigest()
    return int(digest[:8], 16) % BUCKETS


def holdout_threshold(holdout_rate: str) -> int:
    """``holdout_rate × 10,000`` exactly; rates have at most 4 decimals."""
    if _RATE.fullmatch(holdout_rate) is None:
        raise ValueError(
            f"holdout_rate must be a decimal in [0, 1] with <= 4 places: {holdout_rate!r}"
        )
    return int(Decimal(holdout_rate) * BUCKETS)


def is_holdout(run_id: str, holdout_rate: str) -> bool:
    return holdout_bucket(run_id) < holdout_threshold(holdout_rate)
