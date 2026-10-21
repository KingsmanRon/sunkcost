// Deterministic holdout assignment, identical in both SDKs (ARCHITECTURE.md §4 C2, ADR-0002).

import { createHash } from "node:crypto";

export const BUCKETS = 10_000;
const RATE = /^(?:0(?:\.([0-9]{1,4}))?|1(?:\.0{1,4})?)$/;

/** First 8 hex digits of SHA-256(lower-case run_id), as a uint32, modulo 10,000. */
export function holdoutBucket(runId: string): number {
  const digest = createHash("sha256").update(runId.toLowerCase(), "ascii").digest("hex");
  return Number.parseInt(digest.slice(0, 8), 16) % BUCKETS;
}

/** holdout_rate × 10,000 exactly; rates have at most 4 decimals. */
export function holdoutThreshold(holdoutRate: string): number {
  const m = RATE.exec(holdoutRate);
  if (!m) {
    throw new RangeError(
      `holdout_rate must be a decimal in [0, 1] with <= 4 places: ${JSON.stringify(holdoutRate)}`,
    );
  }
  if (holdoutRate.startsWith("1")) return BUCKETS;
  return Number((m[1] ?? "").padEnd(4, "0"));
}

export function isHoldout(runId: string, holdoutRate: string): boolean {
  return holdoutBucket(runId) < holdoutThreshold(holdoutRate);
}
