import Link from "next/link";

import { org, runs, workload } from "@/lib/data";
import { formatUsd, parseDecimal } from "@/lib/decimal";
import { utc } from "@/lib/format";

export default async function RunsPage({ params }: { params: Promise<{ org: string; id: string }> }) {
  const { org: orgId, id } = await params;
  const o = await org(orgId);
  const w = await workload(o.id, id);
  const list = await runs(o.id, w.id);
  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        <Link href={`/o/${o.id}/workloads`}>Workloads</Link> /{" "}
        <Link href={`/o/${o.id}/workloads/${w.id}`}>{w.name}</Link> /
      </p>
      <h1>Runs</h1>
      <p className="muted">The 50 most recent runs.</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Outcome</th>
              <th>Steps</th>
              <th>Cost</th>
              <th>Mode</th>
              <th>Would abort</th>
              <th>Aborted</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id}>
                <td>
                  <Link href={`/o/${o.id}/runs/${r.id}`}>{utc(r.started_at)}</Link>
                </td>
                <td>{r.status}</td>
                <td>{r.outcome ?? <span className="muted">pending</span>}</td>
                <td>{r.steps ?? "—"}</td>
                <td>{r.total_cost_usd ? formatUsd(parseDecimal(r.total_cost_usd), 4) : "—"}</td>
                <td>
                  {r.mode ?? "—"}
                  {r.holdout ? <span className="muted"> (holdout)</span> : null}
                </td>
                <td>{r.would_abort_step ?? "—"}</td>
                <td>{r.aborted_step ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
