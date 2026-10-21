"""API pieces that need no database (part of G2)."""

from __future__ import annotations

import hashlib
import time
from datetime import timezone

import pytest

from sunkcost_api.auth import KEY_FORMAT, new_api_key
from sunkcost_api.ingest import money, ts, validate_envelope
from sunkcost_api.ratelimit import RateLimiter
from sunkcost_api.routes_sdk import bundle_etag


def test_new_api_keys_match_the_documented_format() -> None:
    key, prefix, digest = new_api_key()
    m = KEY_FORMAT.fullmatch(key)
    assert m is not None
    assert m.group(1) == prefix
    assert digest == hashlib.sha256(key.encode()).hexdigest()
    assert len({new_api_key()[0] for _ in range(100)}) == 100


def test_etag_changes_with_signature_and_override() -> None:
    base = bundle_etag("b1", "sig", None)
    assert base == bundle_etag("b1", "sig", None)
    assert base != bundle_etag("b1", "sig2", None)  # a re-signed bundle is new content
    assert base != bundle_etag("b1", "sig", "shadow")
    assert base.startswith('"')
    assert base.endswith('"')


def test_rate_limiter_refills() -> None:
    limiter = RateLimiter(per_minute=60)
    assert all(limiter.take("k") is None for _ in range(60))
    wait = limiter.take("k")
    assert wait is not None
    assert 0 < wait <= 1.0
    assert limiter.take("other") is None  # buckets are per key
    time.sleep(1.05)
    assert limiter.take("k") is None


def test_timestamps_and_money() -> None:
    t = ts("2026-09-23T10:00:00.123456789Z")
    assert (t.tzinfo, t.microsecond) == (timezone.utc, 123456)  # digits beyond µs truncated
    assert str(money("0.002")) == "0.002"
    assert money(None) is None


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"schema_version": "1", "events": []},
        {
            "schema_version": "2",
            "sent_at": "2026-09-23T10:00:00Z",
            "sdk": {"name": "x", "version": "1"},
            "dropped_events": 0,
            "events": [{}],
        },
        {
            "schema_version": "1",
            "sent_at": "2026-09-23T10:00:00Z",
            "sdk": {"name": "x", "version": "1"},
            "dropped_events": -1,
            "events": [{}],
        },
        {
            "schema_version": "1",
            "sent_at": "2026-09-23T10:00:00Z",
            "sdk": {"name": "x", "version": "1"},
            "dropped_events": 0,
            "events": [{}] * 1001,
        },
        {
            "schema_version": "1",
            "sent_at": "2026-09-23T10:00:00Z",
            "sdk": {"name": "x", "version": "1"},
            "dropped_events": 0,
            "events": [{}],
            "extra": True,
        },
    ],
)
def test_invalid_envelopes(doc: object) -> None:
    with pytest.raises(ValueError, match=r"."):  # pydantic's ValidationError is a ValueError
        validate_envelope(doc)


def test_valid_envelope_defers_event_validation() -> None:
    doc = {
        "schema_version": "1",
        "sent_at": "2026-09-23T10:00:00Z",
        "sdk": {"name": "x", "version": "1"},
        "dropped_events": 3,
        "events": [{"type": "bogus"}],
    }
    header, events = validate_envelope(doc)
    assert header.dropped_events == 3
    assert events == [{"type": "bogus"}]
