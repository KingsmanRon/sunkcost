import { describe, expect, it } from "vitest";

import { enforceGate, isShadowWeek, type GateReport } from "@/lib/enforce";

const week: GateReport = {
  period_start: "2026-10-13T00:00:00Z",
  period_end: "2026-10-20T00:00:00Z",
  runs: 400,
  enforced_runs: 0,
  first_run_at: "2026-10-13T02:00:00Z",
};

describe("enforce toggle gate (§4 C9, ADR-0007)", () => {
  it("is open with a validated bundle and a shadow week", () => {
    expect(enforceGate([{ status: "validated" }], [week])).toEqual({ allowed: true, reasons: [] });
    expect(enforceGate([{ status: "active" }], [week]).allowed).toBe(true);
  });

  it("needs a bundle that passed validation", () => {
    const gate = enforceGate([{ status: "rejected" }], [week]);
    expect(gate.allowed).toBe(false);
    expect(gate.reasons).toHaveLength(1);
    expect(enforceGate([], [week]).allowed).toBe(false);
  });

  it("needs a shadow report covering 7 days", () => {
    const gate = enforceGate([{ status: "validated" }], []);
    expect(gate.allowed).toBe(false);
    expect(gate.reasons[0]).toMatch(/7 days/);
  });

  it("counts only reports that are 7 days long, shadow-only, and start with data", () => {
    expect(isShadowWeek(week)).toBe(true);
    expect(isShadowWeek({ ...week, period_start: "2026-10-19T00:00:00Z" })).toBe(false); // one day
    expect(isShadowWeek({ ...week, enforced_runs: 1 })).toBe(false);
    expect(isShadowWeek({ ...week, runs: 0 })).toBe(false);
    expect(isShadowWeek({ ...week, first_run_at: null })).toBe(false);
    // Data only from day 3: a new workload's first week doesn't count early.
    expect(isShadowWeek({ ...week, first_run_at: "2026-10-15T00:00:00Z" })).toBe(false);
  });
});
