from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from sunkcost.core import (
    args_hash,
    datetime_to_us,
    format_timestamp_us,
    holdout_bucket,
    holdout_threshold,
    is_holdout,
    parse_timestamp_us,
)
from sunkcost_spec.resources import VECTORS_DIR


def test_holdout_vectors() -> None:
    doc = json.loads((VECTORS_DIR / "holdout.v1" / "buckets.json").read_text("utf-8"))
    for case in doc["cases"]:
        assert holdout_bucket(case["run_id"]) == case["bucket"]
        assert [is_holdout(case["run_id"], r) for r in doc["rates"]] == case["holdout"]


@pytest.mark.parametrize(
    ("rate", "expected"),
    [("0", 0), ("0.05", 500), ("0.0001", 1), ("1", 10_000), ("1.0000", 10_000)],
)
def test_holdout_threshold(rate: str, expected: int) -> None:
    assert holdout_threshold(rate) == expected


@pytest.mark.parametrize("rate", ["0.00001", "1.5", "-0.1", "0.05 ", ".05", "5%"])
def test_holdout_threshold_rejects(rate: str) -> None:
    with pytest.raises(ValueError, match="holdout_rate"):
        holdout_threshold(rate)


def test_args_hash_vectors() -> None:
    doc = json.loads((VECTORS_DIR / "args-hash.v1" / "cases.json").read_text("utf-8"))
    for case in doc["cases"]:
        assert args_hash(case["salt"], case["args"]) == case["args_hash"], case["name"]
        assert args_hash(case["salt"].encode(), case["args"]) == case["args_hash"]


@pytest.mark.parametrize(
    ("ts", "us"),
    [
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T00:00:00.5Z", 500_000),
        ("2026-09-23T10:00:00.123456789Z", 1_790_157_600_123_456),
        ("1969-12-31T23:59:59.999999Z", -1),
        ("2024-02-29T00:00:00Z", 1_709_164_800_000_000),
    ],
)
def test_parse_timestamp(ts: str, us: int) -> None:
    assert parse_timestamp_us(ts) == us


@pytest.mark.parametrize(
    "ts",
    [
        "2026-02-30T00:00:00Z",
        "2026-09-23T24:00:00Z",
        "2026-09-23T10:00:00+00:00",
        "2026-09-23T10:00:00Z\n",
        "2026-09-23 10:00:00Z",
    ],
)
def test_parse_timestamp_rejects(ts: str) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        parse_timestamp_us(ts)


def test_format_round_trip() -> None:
    for us in [0, 1, -1, 1_790_157_600_123_456, 253_402_300_799_999_999]:
        assert parse_timestamp_us(format_timestamp_us(us)) == us


def test_datetime_to_us() -> None:
    dt = datetime(2026, 9, 23, 10, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert datetime_to_us(dt) == parse_timestamp_us("2026-09-23T08:00:00.123456Z")
    with pytest.raises(ValueError, match="timezone"):
        datetime_to_us(datetime(2026, 1, 1))
