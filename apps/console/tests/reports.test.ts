import { describe, expect, it } from "vitest";

import { formatUsd } from "@/lib/decimal";
import { rateWithInterval } from "@/lib/format";
import { problemMessage } from "@/lib/problem";
import { bars, daily, latestWeek } from "@/lib/reports";

const r = (start: string, end: string, verified = "0", estimated = "0") => ({
  period_start: start,
  period_end: end,
  verified_saving_usd: verified,
  estimated_saving_usd: estimated,
});

describe("reports", () => {
  const list = [
    r("2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z", "2.5"),
    r("2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z", "10"),
    r("2026-10-18T00:00:00Z", "2026-10-19T00:00:00Z", "1.25", "0.5"),
    r("2026-10-13T00:00:00Z", "2026-10-20T00:00:00Z", "11"),
  ];

  it("separates daily reports from trailing weeks", () => {
    expect(daily(list).map((x) => x.period_start.slice(0, 10))).toEqual(["2026-10-18", "2026-10-19"]);
    expect(latestWeek(list)?.verified_saving_usd).toBe("11");
    expect(latestWeek(daily(list))).toBeUndefined();
  });

  it("keeps verified and estimated as separate series", () => {
    const [first] = bars(list);
    expect(first && formatUsd(first.verified)).toBe("$1.25");
    expect(first && formatUsd(first.estimated)).toBe("$0.50");
  });
});

describe("formatting", () => {
  it("shows a rate with its interval", () => {
    expect(rateWithInterval(0.0123, 0.004, 0.031)).toBe("1.2% (0.4–3.1%)");
    expect(rateWithInterval(null, null, null)).toBe("—");
  });

  it("reads RFC 9457 problem documents", () => {
    expect(problemMessage({ title: "Conflict", detail: "only validated bundles" }, 409)).toBe(
      "only validated bundles",
    );
    expect(problemMessage({ title: "Forbidden" }, 403)).toBe("Forbidden");
    expect(problemMessage("oops", 500)).toBe("Request failed (500)");
  });
});
