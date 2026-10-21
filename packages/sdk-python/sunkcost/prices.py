"""Model prices (``prices.v1``) for ``record_step`` calls that give no ``cost_usd`` (ADR-0002 §17).

Lookup is an exact match on the model ID or an alias. The entry used is the latest whose
``effective_from`` is on or before the step's date, or the earliest entry if none is. A long-context
tier, when the step's input exceeds its ``above_input_tokens``, replaces both prices. Arithmetic is
exact, rounded half-even to 9 places.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from importlib import resources
from typing import Any

from sunkcost._money import NINE_PLACES

_PER = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class _Price:
    effective_from: date
    input: Decimal
    output: Decimal
    tiers: tuple[
        tuple[int, Decimal, Decimal], ...
    ]  # (above_input_tokens, input, output), ascending


class PriceTable:
    def __init__(self, doc: Mapping[str, Any]) -> None:
        if doc.get("schema_version") != "1" or doc.get("unit") != "per_1m_tokens":
            raise ValueError("not a prices.v1 table")
        self._by_model: dict[str, tuple[_Price, ...]] = {}
        for entry in doc["models"]:
            prices = tuple(
                sorted(
                    (
                        _Price(
                            effective_from=date.fromisoformat(p["effective_from"]),
                            input=Decimal(p["input"]),
                            output=Decimal(p["output"]),
                            tiers=tuple(
                                sorted(
                                    (
                                        int(t["above_input_tokens"]),
                                        Decimal(t["input"]),
                                        Decimal(t["output"]),
                                    )
                                    for t in p["tiers"]
                                )
                            ),
                        )
                        for p in entry["prices"]
                    ),
                    key=lambda p: p.effective_from,
                )
            )
            for name in (entry["model"], *entry["aliases"]):
                self._by_model[name] = prices

    @classmethod
    def bundled(cls) -> PriceTable:
        """The table shipped with this SDK version (a copy of ``packages/spec/prices``)."""
        text = resources.files("sunkcost").joinpath("data/prices.v1.json").read_text("utf-8")
        return cls(json.loads(text))

    def __contains__(self, model: object) -> bool:
        return model in self._by_model

    def cost(self, model: str, input_tokens: int, output_tokens: int, on: date) -> Decimal | None:
        """USD cost of one call, or ``None`` when the model is not in the table."""
        prices = self._by_model.get(model)
        if prices is None:
            return None
        price = prices[0]
        for p in prices:
            if p.effective_from <= on:
                price = p
        unit_in, unit_out = price.input, price.output
        for above, tier_in, tier_out in price.tiers:
            if input_tokens > above:
                unit_in, unit_out = tier_in, tier_out
        total = (Decimal(input_tokens) * unit_in + Decimal(output_tokens) * unit_out) / _PER
        return total.quantize(NINE_PLACES, rounding=ROUND_HALF_EVEN)
