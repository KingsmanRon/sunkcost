"""Canonical JSON (RFC 8785).

``canonical_json(obj)`` is the form that bundles are signed over: objects, arrays, strings, integers
within ±(2^53 − 1), booleans and null. Floats are refused, because signed content carries every
non-integer as a decimal string. ``canonical_json(obj, allow_floats=True)`` adds RFC 8785's
ECMAScript number serialisation; tool arguments are hashed in that form (ADR-0002).
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalJSONError(ValueError):
    """The value has no canonical form under the requested rules."""


def canonical_json(value: Any, *, allow_floats: bool = False) -> bytes:
    parts: list[str] = []
    _emit(value, allow_floats, parts)
    try:
        return "".join(parts).encode("utf-8")
    except UnicodeEncodeError as exc:  # an unpaired surrogate slipped through as a key
        raise CanonicalJSONError("unpaired surrogate") from exc


def _emit(value: Any, allow_floats: bool, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            out.append(str(value))
        elif allow_floats:
            out.append(ecmascript_number(float(value)))
        else:
            raise CanonicalJSONError("integer outside the IEEE-754 safe range")
    elif isinstance(value, float):
        if not allow_floats:
            raise CanonicalJSONError("floats are not allowed in signed content")
        out.append(ecmascript_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, allow_floats, out)
        out.append("]")
    elif isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalJSONError("object keys must be strings")
        out.append("{")
        # RFC 8785 section 3.2.3: sort by UTF-16 code units.
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _emit(value[key], allow_floats, out)
        out.append("}")
    else:
        raise CanonicalJSONError(f"unsupported type {type(value).__name__}")


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def _string(value: str) -> str:
    # json.dumps with ensure_ascii=False escapes exactly what RFC 8785 requires: '"', '\\', the
    # short forms \b \f \n \r \t, and every other U+0000-U+001F as lower-case \u00xx.
    for ch in value:
        if "\ud800" <= ch <= "\udfff":
            raise CanonicalJSONError("unpaired surrogate")
    return json.dumps(value, ensure_ascii=False)


def ecmascript_number(x: float) -> str:
    """ECMAScript ``Number::toString`` for a finite float64 (RFC 8785 section 3.2.2.3)."""
    if not math.isfinite(x):
        raise CanonicalJSONError("NaN and Infinity have no JSON form")
    if x == 0.0:
        return "0"
    if x < 0.0:
        return "-" + ecmascript_number(-x)
    # repr() yields the shortest digit string that round-trips, as ECMAScript requires.
    _, digit_tuple, exponent = Decimal(repr(x)).as_tuple()
    if not isinstance(exponent, int):  # pragma: no cover - excluded by isfinite above
        raise CanonicalJSONError("non-finite number")
    digits = "".join(str(d) for d in digit_tuple).rstrip("0") or "0"
    exponent += len(digit_tuple) - len(digits)
    k = len(digits)
    n = exponent + k
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
