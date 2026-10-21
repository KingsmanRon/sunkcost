/** The human-readable part of an RFC 9457 problem document. */
export function problemMessage(problem: unknown, status: number): string {
  if (problem && typeof problem === "object") {
    const p = problem as { detail?: unknown; title?: unknown };
    if (typeof p.detail === "string" && p.detail) return p.detail;
    if (typeof p.title === "string" && p.title) return p.title;
  }
  return `Request failed (${String(status)})`;
}
