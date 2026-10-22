import Link from "next/link";

import { createWorkload } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { bundles, isAdmin, org, projects, reports, workloads } from "@/lib/data";
import { formatUsd, parseDecimal } from "@/lib/decimal";
import { rateWithInterval } from "@/lib/format";
import { latestWeek } from "@/lib/reports";

export default async function WorkloadsPage({ params }: { params: Promise<{ org: string }> }) {
  const o = await org((await params).org);
  const [list, projectList] = await Promise.all([workloads(o.id), projects(o.id)]);
  const ids = list.map((w) => w.id);
  const [allBundles, allReports] = await Promise.all([bundles(o.id, ids), reports(o.id, ids, 14)]);
  const projectNames = new Map(projectList.map((p) => [p.id, p.name]));
  return (
    <>
      <h1>Workloads</h1>
      <p className="muted">
        A workload is one kind of agent run. 7-day figures come from the latest trailing-week
        report.
      </p>
      {list.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Workload</th>
                <th>Project</th>
                <th>Mode</th>
                <th>Active bundle</th>
                <th>Verified savings (7 d)</th>
                <th>False-abort rate (7 d, 95% CI)</th>
              </tr>
            </thead>
            <tbody>
              {list.map((w) => {
                const active = allBundles.find((b) => b.workload_id === w.id && b.status === "active");
                const week = latestWeek(allReports.filter((r) => r.workload_id === w.id));
                return (
                  <tr key={w.id}>
                    <td>
                      <Link href={`/o/${o.id}/workloads/${w.id}`}>{w.name}</Link>
                    </td>
                    <td>{projectNames.get(w.project_id) ?? "—"}</td>
                    <td>
                      <span className={`badge ${w.mode}`}>{w.mode}</span>{" "}
                      {w.override ? <span className="badge override">override: shadow</span> : null}
                    </td>
                    <td>{active ? `v${String(active.version)}` : "—"}</td>
                    <td>{week ? formatUsd(parseDecimal(week.verified_saving_usd)) : "—"}</td>
                    <td>
                      {week ? rateWithInterval(week.false_abort_rate, week.fa_ci_low, week.fa_ci_high) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">
          No workloads yet. Create one here, or start sending runs: a workload is created on first
          use.
        </p>
      )}
      {isAdmin(o) && projectList.length ? (
        <>
          <h2>New workload</h2>
          <div className="panel">
            <ActionForm action={createWorkload} submit="Create workload" hidden={{ org: o.id }} inline>
              <label>
                Project
                <select name="project_id" required>
                  {projectList.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Name
                <input type="text" name="name" required pattern="[A-Za-z0-9][A-Za-z0-9._\-]{0,127}" placeholder="support.triage" />
              </label>
            </ActionForm>
          </div>
        </>
      ) : null}
    </>
  );
}
