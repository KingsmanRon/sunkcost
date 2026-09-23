from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sunkcost.core import CanonicalJSONError, canonical_json, ecmascript_number
from sunkcost_spec.resources import VECTORS_DIR

CANONICAL = VECTORS_DIR / "canonical-json"
VECTORS = sorted(p for p in CANONICAL.glob("*.json") if p.name != "must-reject.json")


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_canonical_bytes_match_vectors(path: Path) -> None:
    doc = json.loads(path.read_text("utf-8"))
    assert canonical_json(doc["input"]).hex() == doc["canonical_hex"]


def test_must_reject_vectors() -> None:
    doc = json.loads((CANONICAL / "must-reject.json").read_text("utf-8"))
    for case in doc["cases"]:
        with pytest.raises(CanonicalJSONError):
            canonical_json(json.loads(case["json_text"]))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, b"true"),
        (1, b"1"),
        ((1, 2), b"[1,2]"),
        ({"b": [], "a": {}}, b'{"a":{},"b":[]}'),
        ("\u2028/\u007f", '"\u2028/\u007f"'.encode()),
        (-(2**53 - 1), b"-9007199254740991"),
    ],
)
def test_canonical_examples(value: Any, expected: bytes) -> None:
    assert canonical_json(value) == expected


@pytest.mark.parametrize(
    "value", [1.5, 1.0, float("nan"), 2**53, {1: "x"}, b"bytes", {"x": object()}]
)
def test_signed_content_rejects(value: Any) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json(value)


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        # Outputs of ECMAScript Number.prototype.toString.
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-1.5, "-1.5"),
        (0.1, "0.1"),
        (0.1 + 0.2, "0.30000000000000004"),
        (123.456, "123.456"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (123e-20, "1.23e-18"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (2.0**53, "9007199254740992"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
    ],
)
def test_ecmascript_number(x: float, expected: str) -> None:
    assert ecmascript_number(x) == expected


def test_allow_floats_mode() -> None:
    assert (
        canonical_json({"x": 2.5, "n": 2**64}, allow_floats=True)
        == b'{"n":18446744073709552000,"x":2.5}'
    )
    with pytest.raises(CanonicalJSONError):
        canonical_json(float("inf"), allow_floats=True)
