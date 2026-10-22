import { parseDecimal } from "@/lib/decimal";

export interface ReportLike {
  period_start: string;
  period_end: string;
  verified_saving_usd: string;
  estimated_saving_usd: string;
}

const DAY_MS = 86_400_000;

function length(r: ReportLike): number {
  return Date.parse(r.period_end) - Date.parse(r.period_start);
}

/** Daily reports, oldest first. */
export function daily<T extends ReportLike>(reports: readonly T[]): T[] {
  return reports
    .filter((r) => length(r) === DAY_MS)
    .sort((a, b) => Date.parse(a.period_start) - Date.parse(b.period_start));
}

/** The most recent trailing-week report (≥ 7 days), which holds the 7-day figures and interval. */
export function latestWeek<T extends ReportLike>(reports: readonly T[]): T | undefined {
  return reports
    .filter((r) => length(r) >= 7 * DAY_MS)
    .sort((a, b) => Date.parse(b.period_end) - Date.parse(a.period_end))[0];
}

export interface Bar {
  label: string;
  verified: bigint;
  estimated: bigint;
}

export function bars(reports: readonly ReportLike[]): Bar[] {
  return daily(reports).map((r) => ({
    label: r.period_start.slice(0, 10),
    verified: parseDecimal(r.verified_saving_usd),
    estimated: parseDecimal(r.estimated_saving_usd),
  }));
}
