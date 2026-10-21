"""USD amounts as exact decimals: decimal strings on the wire, never floats (CLAUDE.md, §6.1)."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

NINE_PLACES = Decimal("0.000000001")
# numeric(20,9): at most 11 integer digits.
MAX_MONEY = Decimal("99999999999.999999999")
ZERO = Decimal(0)


def to_money(value: object) -> Decimal | None:
    """A caller-supplied amount as a 9-place decimal, or ``None`` when it is not a valid amount.

    Accepts ``Decimal``, ``int`` and decimal strings. A ``float`` is taken at its shortest
    round-trip decimal form (``repr``), so ``0.1`` means 0.1, not 0.1000000000000000055….
    """
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            d = Decimal(repr(value))
        elif isinstance(value, (int, Decimal)):
            d = Decimal(value)
        elif isinstance(value, str):
            d = Decimal(value.strip())
        else:
            return None
        if not d.is_finite() or d < 0:
            return None
        d = d.quantize(NINE_PLACES, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError):
        return None
    return d if d <= MAX_MONEY else None


def money_str(value: Decimal) -> str:
    """Wire form: plain decimal, no exponent, at most 9 fractional digits, no trailing zeros."""
    text = format(value.quantize(NINE_PLACES, rounding=ROUND_HALF_EVEN), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
