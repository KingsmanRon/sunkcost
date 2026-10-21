from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from sunkcost._ids import is_uuid7, uuid7
from sunkcost._money import money_str, to_money
from sunkcost.prices import PriceTable
from sunkcost_spec.resources import PRICES_PATH

TABLE = {
    "schema_version": "1",
    "currency": "USD",
    "unit": "per_1m_tokens",
    "models": [
        {
            "provider": "example",
            "model": "m-1",
            "aliases": ["m-1-2026"],
            "prices": [
                {
                    "effective_from": "2026-01-01",
                    "input": "3",
                    "output": "15",
                    "tiers": [],
                    "source_url": "https://example.com",
                    "retrieved_at": "2026-01-01",
                },
                {
                    "effective_from": "2026-06-01",
                    "input": "2",
                    "output": "10",
                    "tiers": [{"above_input_tokens": 200000, "input": "4", "output": "20"}],
                    "source_url": "https://example.com",
                    "retrieved_at": "2026-06-01",
                },
            ],
        }
    ],
}


def test_bundled_table_is_the_spec_table() -> None:
    assert (
        PriceTable.bundled()._by_model.keys()
        == PriceTable(json.loads(PRICES_PATH.read_text()))._by_model.keys()
    )
    shipped = json.loads(
        (PRICES_PATH.parents[2] / "sdk-python" / "sunkcost" / "data" / "prices.v1.json").read_text()
    )
    assert shipped == json.loads(PRICES_PATH.read_text())


@pytest.mark.parametrize(
    ("model", "tin", "tout", "on", "expected"),
    [
        ("m-1", 1_000_000, 0, date(2026, 3, 1), "3.000000000"),
        ("m-1-2026", 1000, 1000, date(2026, 3, 1), "0.018000000"),
        ("m-1", 1000, 1000, date(2026, 7, 1), "0.012000000"),  # later entry
        ("m-1", 1000, 1000, date(2025, 1, 1), "0.018000000"),  # before the first: earliest
        ("m-1", 200_000, 0, date(2026, 7, 1), "0.400000000"),  # at the tier boundary: base price
        ("m-1", 200_001, 1, date(2026, 7, 1), "0.800024000"),  # above it: tier replaces both
        ("m-1", 1, 0, date(2026, 7, 1), "0.000002000"),
        ("m-1", 3, 0, date(2026, 7, 1), "0.000006000"),
    ],
)
def test_price_lookup(model: str, tin: int, tout: int, on: date, expected: str) -> None:
    assert PriceTable(TABLE).cost(model, tin, tout, on) == Decimal(expected)


def test_unknown_model_has_no_cost() -> None:
    assert PriceTable(TABLE).cost("gpt-x", 10, 10, date(2026, 7, 1)) is None


def test_half_even_rounding() -> None:
    table = json.loads(json.dumps(TABLE))
    table["models"][0]["prices"][1]["input"] = "0.0005"
    # 1 token × 0.0005 / 1e6 = 5e-10, which rounds half-even to 0 at 9 places.
    assert PriceTable(table).cost("m-1", 1, 0, date(2026, 7, 1)) == Decimal("0")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.002", "0.002"),
        (Decimal("1.0000000005"), "1"),  # half-even
        (Decimal("1.0000000015"), "1.000000002"),
        (0.1, "0.1"),  # a float means its shortest decimal form
        (3, "3"),
        ("0", "0"),
        (-1, None),
        ("nan", None),
        (float("inf"), None),
        ("1e12", None),  # beyond numeric(20,9)
        (True, None),
        ([1], None),
    ],
)
def test_money(value: object, expected: str | None) -> None:
    m = to_money(value)
    assert (None if m is None else money_str(m)) == expected


def test_uuid7() -> None:
    ids = {uuid7() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(is_uuid7(i) for i in ids)
    assert uuid7(0).startswith("00000000-0000-7")
    assert not is_uuid7("01a03336-78b8-4c55-b624-8e5a0fac9d43")  # version 4
