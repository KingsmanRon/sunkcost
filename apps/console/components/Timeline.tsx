import type { Step } from "@/lib/data";

const HEIGHT = 180;
const PAD = 28;

/** Per-step score against the bundle's threshold, with the would-abort point marked (§4 C9). */
export function Timeline({ steps, wouldAbortStep }: { steps: Step[]; wouldAbortStep: number | null }) {
  const scored = steps.filter((s) => s.score !== null);
  if (scored.length === 0) {
    return (
      <p className="muted">
        No scores recorded: the SDK had no bundle for this run, or check() wasn’t called.
      </p>
    );
  }
  const n = Math.max(...steps.map((s) => s.seq));
  const step = Math.max(6, Math.min(24, 900 / Math.max(n, 1)));
  const width = PAD + n * step + 16;
  const x = (seq: number) => PAD + (seq - 0.5) * step;
  const y = (v: number) => 8 + (1 - Math.min(Math.max(v, 0), 1)) * (HEIGHT - 16);
  const score = scored.map((s) => `${String(x(s.seq))},${String(y(s.score ?? 0))}`).join(" ");
  const threshold = scored
    .filter((s) => s.threshold !== null)
    .map((s) => `${String(x(s.seq) - step / 2)},${String(y(s.threshold ?? 1))} ${String(x(s.seq) + step / 2)},${String(y(s.threshold ?? 1))}`)
    .join(" ");
  return (
    <div className="table-wrap">
      <svg width={width} height={HEIGHT + 24} role="img" aria-label="Score against threshold per step">
        <text x={0} y={y(1) + 4}>1</text>
        <text x={0} y={y(0) + 4}>0</text>
        <line x1={PAD} x2={width} y1={y(0)} y2={y(0)} stroke="var(--line)" />
        {threshold ? (
          <polyline points={threshold} fill="none" stroke="var(--threshold)" strokeDasharray="4 3" data-testid="threshold" />
        ) : null}
        <polyline points={score} fill="none" stroke="var(--accent)" strokeWidth={2} />
        {scored.map((s) => (
          <circle key={s.seq} cx={x(s.seq)} cy={y(s.score ?? 0)} r={2.5} fill="var(--accent)">
            <title>{`step ${String(s.seq)}: score ${(s.score ?? 0).toFixed(3)}${s.threshold === null ? "" : `, threshold ${s.threshold.toFixed(3)}`}`}</title>
          </circle>
        ))}
        {wouldAbortStep !== null ? (
          <g data-testid="would-abort">
            <line x1={x(wouldAbortStep)} x2={x(wouldAbortStep)} y1={4} y2={HEIGHT} stroke="var(--threshold)" />
            <text x={x(wouldAbortStep) + 4} y={14}>
              {`would abort at step ${String(wouldAbortStep)}`}
            </text>
          </g>
        ) : null}
      </svg>
    </div>
  );
}
