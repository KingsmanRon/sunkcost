"""Tool-argument hashing: the only form in which arguments leave the process (§9, ADR-0002)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from sunkcost.core.canonical import canonical_json

PREFIX = "hmac-sha256:"


def args_hash(salt: str | bytes, args: Any) -> str:
    """``hmac-sha256:`` + hex HMAC-SHA256 keyed with the salt over JCS(args), floats allowed."""
    key = salt.encode("utf-8") if isinstance(salt, str) else salt
    digest = hmac.new(key, canonical_json(args, allow_floats=True), hashlib.sha256).hexdigest()
    return PREFIX + digest
