import Link from "next/link";

import { Timeline } from "@/components/Timeline";
import { org, run, steps, workload } from "@/lib/data";
import { formatUsd, parseDecimal } from "@/lib/decimal";
import { utc } from "@/lib/format";

export default async function RunPage({ params }: { params: Promise<{ org: string; run: string }> }) {
  const { org: orgId, run: runId } = await params;
  const o = await org(orgId);
  const r = await run(o.id, runId);
  const [w, timeline] = await Promise.all([workload(o.id, r.workload_id), steps(o.id, r.id)]);
  const wouldAbort = r.would_abort_step ?? timeline.find((s) => s.would_abort)?.seq ?? null;
  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        <Link href={`/o/${o.id}/workloads`}>Workloads</Link> /{" "}
        <Link href={`/o/${o.id}/workloads/${w.id}`}>{w.name}</Link> /{" "}
        <Link href={`/o/${o.id}/workloads/${w.id}/runs`}>Runs</Link> /
      </p>
      <h1>
        Run <code>{r.id}</code>
      </h1>
      <p className="muted">
        Started {utc(r.started_at)} · {r.status} · outcome {r.outcome ?? "pending"} · mode{" "}
        {r.mode ?? "—"}
        {r.holdout ? " (holdout)" : ""} · bundle {r.bundle_version ? `v${String(r.bundle_version)}` : "none"}
      </p>
      <h2>Score against threshold</h2>
      <div className="panel">
        <Timeline steps={timeline} wouldAbortStep={wouldAbort} />
      </div>
      <h2>Steps</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Time</th>
              <th>Kind</th>
              <th>Model or tool</th>
              <th>Cost</th>
              <th>Error</th>
              <th>Decision</th>
              <th>Score</th>
              <th>Threshold</th>
            </tr>
          </thead>
          <tbody>
            {timeline.map((s) => (
              <tr key={s.seq}>
                <td>{s.seq}</td>
                <td>{utc(s.ts)}</td>
                <td>{s.kind}</td>
                <td>
                  <code>{s.kind === "model" ? s.model : s.tool_name}</code>
                </td>
                <td>{s.cost_usd ? formatUsd(parseDecimal(s.cost_usd), 6) : "—"}</td>
                <td>{s.error ? "yes" : ""}</td>
                <td>
                  {s.action ?? "—"}
                  {s.would_abort ? <span className="error"> (would abort)</span> : null}
                  {s.reason ? <div className="muted">{s.reason}</div> : null}
                </td>
                <td>{s.score === null ? "—" : s.score.toFixed(3)}</td>
                <td>{s.threshold === null ? "—" : s.threshold.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
