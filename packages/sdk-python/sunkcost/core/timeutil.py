"""UTC RFC 3339 timestamps as integer microseconds since the Unix epoch.

Wire timestamps end in a literal ``Z`` and carry 0-9 fractional digits; digits beyond the sixth are
truncated (features.v1.md). Integer microseconds keep the elapsed-time feature exact across Python
and TypeScript.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z"
)
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_US_PER_DAY = 86_400_000_000


def parse_timestamp_us(value: str) -> int:
    """Parse ``YYYY-MM-DDTHH:MM:SS[.fraction]Z`` to microseconds since the epoch."""
    m = _TIMESTAMP.fullmatch(value)
    if m is None:
        raise ValueError(f"not a UTC RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"time of day out of range in timestamp {value!r}")
    try:
        days = date(year, month, day).toordinal() - _EPOCH_ORDINAL
    except ValueError:
        raise ValueError(f"invalid calendar date in timestamp {value!r}") from None
    fraction = int((m.group(7) or "").ljust(6, "0")[:6])
    return days * _US_PER_DAY + ((hour * 60 + minute) * 60 + second) * 1_000_000 + fraction


def format_timestamp_us(us: int) -> str:
    """Format microseconds since the epoch as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    days, rem = divmod(us, _US_PER_DAY)
    d = date.fromordinal(days + _EPOCH_ORDINAL)
    seconds, micros = divmod(rem, 1_000_000)
    hour, rem_s = divmod(seconds, 3600)
    minute, second = divmod(rem_s, 60)
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:{second:02d}.{micros:06d}Z"


def datetime_to_us(value: datetime) -> int:
    """Microseconds since the epoch for an aware datetime (for example a Postgres timestamptz)."""
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
