import { describe, expect, it } from "vitest";

import { formatTimestampUs, parseTimestampUs } from "../src/core/time.js";
import { BUNDLED_PRICES } from "../src/data/prices.js";
import { isUuid7, uuid7 } from "../src/ids.js";
import { moneyStr, toMoney } from "../src/money.js";
import { PriceTable } from "../src/prices.js";
import { pyFloatRepr } from "../src/repr.js";
import { specJson } from "./helpers.js";

const TABLE = {
  schema_version: "1",
  currency: "USD",
  unit: "per_1m_tokens",
  models: [
    {
      provider: "example",
      model: "m-1",
      aliases: ["m-1-2026"],
      prices: [
        { effective_from: "2026-01-01", input: "3", output: "15", tiers: [] },
        {
          effective_from: "2026-06-01",
          input: "2",
          output: "10",
          tiers: [{ above_input_tokens: 200000, input: "4", output: "20" }],
        },
      ],
    },
  ],
};

describe("prices (same cases as the Python SDK)", () => {
  it("ships exactly the spec table", () => {
    expect(BUNDLED_PRICES).toEqual(specJson("prices/prices.v1.json"));
  });

  it.each([
    ["m-1", 1_000_000, 0, "2026-03-01", "3"],
    ["m-1-2026", 1000, 1000, "2026-03-01", "0.018"],
    ["m-1", 1000, 1000, "2026-07-01", "0.012"], // later entry
    ["m-1", 1000, 1000, "2025-01-01", "0.018"], // before the first: earliest
    ["m-1", 200_000, 0, "2026-07-01", "0.4"], // at the tier boundary: base price
    ["m-1", 200_001, 1, "2026-07-01", "0.800024"], // above it: the tier replaces both
    ["m-1", 1, 0, "2026-07-01", "0.000002"],
    ["m-1", 3, 0, "2026-07-01", "0.000006"],
  ])("%s %i in, %i out on %s costs %s", (model, tin, tout, on, expected) => {
    const cost = new PriceTable(TABLE).cost(model, tin, tout, on);
    expect(cost === null ? null : moneyStr(cost)).toBe(expected);
  });

  it("has no cost for unknown models", () => {
    expect(new PriceTable(TABLE).cost("gpt-x", 10, 10, "2026-07-01")).toBeNull();
  });

  it("rounds half to even at 9 places", () => {
    const table = structuredClone(TABLE);
    const later = table.models[0]?.prices[1];
    if (later) later.input = "0.0005";
    // 1 token × 0.0005 / 1e6 = 5e-10, which rounds half-even to 0 at 9 places.
    expect(new PriceTable(table).cost("m-1", 1, 0, "2026-07-01")).toBe(0n);
  });
});

describe("money", () => {
  it.each([
    ["0.002", "0.002"],
    ["1.0000000005", "1"], // half-even
    ["1.0000000015", "1.000000002"],
    [0.1, "0.1"], // a number means its shortest decimal form
    [3, "3"],
    [3n, "3"],
    ["0", "0"],
    [-1, null],
    ["nan", null],
    [Number.POSITIVE_INFINITY, null],
    ["1e12", null], // beyond numeric(20,9)
    [true, null],
    [[1], null],
  ])("%s → %s", (value, expected) => {
    const m = toMoney(value);
    expect(m === null ? null : moneyStr(m)).toBe(expected);
  });
});

describe("ids, time and float repr", () => {
  it("generates UUIDv7 with the given millisecond prefix", () => {
    const id = uuid7(1_790_000_000_000);
    expect(isUuid7(id)).toBe(true);
    expect(id.slice(0, 13).replace("-", "")).toBe((1_790_000_000_000).toString(16).padStart(12, "0"));
    expect(new Set(Array.from({ length: 100 }, () => uuid7())).size).toBe(100);
  });

  it("parses and formats timestamps exactly", () => {
    expect(parseTimestampUs("1970-01-01T00:00:00Z")).toBe(0);
    expect(parseTimestampUs("2026-09-23T10:00:01.2Z")).toBe(1_790_157_601_200_000);
    expect(parseTimestampUs("2026-09-23T10:00:01.123456789Z")).toBe(1_790_157_601_123_456);
    expect(formatTimestampUs(1_790_157_601_200_000)).toBe("2026-09-23T10:00:01.200000Z");
    expect(formatTimestampUs(parseTimestampUs("2024-02-29T23:59:59.999999Z"))).toBe("2024-02-29T23:59:59.999999Z");
    for (const bad of ["2026-02-30T00:00:00Z", "2026-09-23T24:00:00Z", "2026-09-23 10:00:00Z", "2026-09-23T10:00:00+00:00"]) {
      expect(() => parseTimestampUs(bad)).toThrow(RangeError);
    }
  });

  it.each([
    [0.5, "0.5"],
    [1, "1.0"],
    [1e-5, "1e-05"],
    [0.0001, "0.0001"],
    [1e16, "1e+16"],
    [1234567890123456, "1234567890123456.0"],
    [0.0003830310264159049, "0.0003830310264159049"],
    [5e-324, "5e-324"],
    [-2.5, "-2.5"],
    [1.7976931348623157e308, "1.7976931348623157e+308"],
  ])("repr(%s) is %s, as in Python", (x, expected) => {
    expect(pyFloatRepr(x)).toBe(expected);
    expect(Number(pyFloatRepr(x))).toBe(x);
  });
});
