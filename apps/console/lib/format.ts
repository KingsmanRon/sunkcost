/** A rate (0..1) as a percentage with one decimal, e.g. 0.0123 → "1.2%". Rates are not money. */
export function percent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

/** "1.2% (0.4–3.1%)": a false-abort rate with its Clopper–Pearson interval. */
export function rateWithInterval(
  rate: number | null | undefined,
  low: number | null | undefined,
  high: number | null | undefined,
): string {
  if (rate === null || rate === undefined) return "—";
  if (low === null || low === undefined || high === null || high === undefined) {
    return percent(rate);
  }
  return `${percent(rate)} (${percent(low).replace("%", "")}–${percent(high)})`;
}

/** UTC ISO-8601 down to the minute, e.g. "2026-10-21 14:05 UTC". */
export function utc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function day(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}
