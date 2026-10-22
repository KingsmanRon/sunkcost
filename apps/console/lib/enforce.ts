// The enforce toggle stays disabled until the workload has a validated bundle and a shadow report
// covering at least 7 days (§4 C9). ADR-0007 defines both precisely.

export interface GateBundle {
  status: string;
}

export interface GateReport {
  period_start: string;
  period_end: string;
  runs: number;
  enforced_runs: number;
  first_run_at: string | null;
}

export interface Gate {
  allowed: boolean;
  reasons: string[];
}

const DAY_MS = 86_400_000;
export const SHADOW_DAYS = 7;

/** Bundles that passed validation at some point: validated now, active, or retired after use. */
const PASSED = new Set(["validated", "active", "retired"]);

/**
 * A report is shadow evidence for 7 days when it spans ≥ 7 days, has runs, none of them enforced,
 * and its runs begin within its first day (so a new workload's first week doesn't count early).
 */
export function isShadowWeek(report: GateReport): boolean {
  const start = Date.parse(report.period_start);
  const end = Date.parse(report.period_end);
  if (report.first_run_at === null || report.runs <= 0 || report.enforced_runs > 0) return false;
  return end - start >= SHADOW_DAYS * DAY_MS && Date.parse(report.first_run_at) < start + DAY_MS;
}

export function enforceGate(bundles: readonly GateBundle[], reports: readonly GateReport[]): Gate {
  const reasons: string[] = [];
  if (!bundles.some((b) => PASSED.has(b.status))) {
    reasons.push("No validated bundle yet. Run a calibration first.");
  }
  if (!reports.some(isShadowWeek)) {
    reasons.push(`No shadow report covering ${String(SHADOW_DAYS)} days yet.`);
  }
  return { allowed: reasons.length === 0, reasons };
}
