"""UUIDv7 run IDs (RFC 9562 §5.7), generated client-side (§2)."""

from __future__ import annotations

import re
import secrets
import time

UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def uuid7(unix_ms: int | None = None) -> str:
    """48-bit Unix milliseconds, version 7, 74 random bits, RFC 4122 variant; lower-case."""
    ms = time.time_ns() // 1_000_000 if unix_ms is None else unix_ms
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def is_uuid7(value: object) -> bool:
    return isinstance(value, str) and UUID7.fullmatch(value) is not None
