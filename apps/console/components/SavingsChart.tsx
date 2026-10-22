import { formatUsd, scaleTo } from "@/lib/decimal";
import type { Bar } from "@/lib/reports";

const HEIGHT = 160;
const BAR = 10;
const GAP = 8;

/** Verified and estimated savings per day as side-by-side bars, never stacked (§4 C9). */
export function SavingsChart({ data }: { data: Bar[] }) {
  if (data.length === 0) {
    return <p className="muted">No daily reports yet. The worker builds one each night.</p>;
  }
  const max = data.reduce((m, d) => (d.verified > m ? d.verified : d.estimated > m ? d.estimated : m), 0n);
  const group = BAR * 2 + GAP;
  const width = Math.max(320, data.length * group + 48);
  return (
    <figure style={{ margin: 0 }}>
      <div className="legend">
        <span>
          <span className="swatch" style={{ background: "var(--verified)" }} />
          Verified (observed spend of caught failures)
        </span>
        <span>
          <span className="swatch" style={{ background: "var(--estimated)" }} />
          Estimated (enforced aborts)
        </span>
      </div>
      <div className="table-wrap">
        <svg
          width={width}
          height={HEIGHT + 34}
          role="img"
          aria-label="Savings per day: verified and estimated"
        >
          <text x={0} y={12}>
            {formatUsd(max)}
          </text>
          <line x1={44} x2={width} y1={HEIGHT} y2={HEIGHT} stroke="var(--line)" />
          {data.map((d, i) => {
            const x = 48 + i * group;
            const hv = scaleTo(d.verified, max, HEIGHT - 16);
            const he = scaleTo(d.estimated, max, HEIGHT - 16);
            return (
              <g key={d.label}>
                <rect x={x} y={HEIGHT - hv} width={BAR} height={hv} fill="var(--verified)">
                  <title>{`${d.label} verified ${formatUsd(d.verified)}`}</title>
                </rect>
                <rect x={x + BAR} y={HEIGHT - he} width={BAR} height={he} fill="var(--estimated)">
                  <title>{`${d.label} estimated ${formatUsd(d.estimated)}`}</title>
                </rect>
                {i % Math.ceil(data.length / 8) === 0 ? (
                  <text x={x} y={HEIGHT + 16}>
                    {d.label.slice(5)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
    </figure>
  );
}
