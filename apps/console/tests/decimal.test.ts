import { describe, expect, it } from "vitest";

import { formatUsd, parseDecimal, scaleTo, sumDecimals } from "@/lib/decimal";

describe("decimal strings", () => {
  it("parses numeric(20,9) text exactly", () => {
    expect(parseDecimal("1.100000000")).toBe(1_100_000_000n);
    expect(parseDecimal("0.000000001")).toBe(1n);
    expect(parseDecimal("12")).toBe(12_000_000_000n);
    expect(parseDecimal("-0.5")).toBe(-500_000_000n);
    expect(parseDecimal(null)).toBe(0n);
  });

  it("refuses anything that is not a plain decimal", () => {
    expect(() => parseDecimal("1e3")).toThrow();
    expect(() => parseDecimal("NaN")).toThrow();
  });

  it("sums without float error", () => {
    // 0.1 + 0.2 in floats is 0.30000000000000004.
    expect(sumDecimals(["0.1", "0.2"])).toBe(parseDecimal("0.3"));
  });

  it("formats dollars, rounding half away from zero", () => {
    expect(formatUsd(parseDecimal("1234.565"))).toBe("$1,234.57");
    expect(formatUsd(parseDecimal("0.004"))).toBe("$0.00");
    expect(formatUsd(parseDecimal("-2.5"))).toBe("-$2.50");
    expect(formatUsd(parseDecimal("0.000123"), 6)).toBe("$0.000123");
  });

  it("scales bars proportionally", () => {
    expect(scaleTo(50n, 100n, 160)).toBe(80);
    expect(scaleTo(0n, 100n, 160)).toBe(0);
    expect(scaleTo(5n, 0n, 160)).toBe(0);
  });
});
