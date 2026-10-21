// Errors thrown into agent code only by adapters configured with raiseOnAbort (§4 C2/C3).

import type { Decision } from "./decision.js";

export class RunAborted extends Error {
  override name = "RunAborted";
  constructor(
    readonly decision: Decision,
    readonly runId: string,
  ) {
    super(
      `sunkcost aborted run ${runId}: score ${String(decision.score)} >= threshold ${String(decision.threshold)}`,
    );
  }
}

/** The RunAborted that `error` is or wraps (via `cause`), if any. Frameworks often re-wrap errors. */
export function findAbort(error: unknown): RunAborted | null {
  const seen = new Set<unknown>();
  let current: unknown = error;
  while (current !== null && current !== undefined && !seen.has(current)) {
    if (current instanceof RunAborted) return current;
    seen.add(current);
    current = current instanceof Error ? current.cause : undefined;
  }
  return null;
}
