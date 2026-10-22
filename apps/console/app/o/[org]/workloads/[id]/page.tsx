import Link from "next/link";

import { activateBundle, calibrate, setMode, setOverride } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { SavingsChart } from "@/components/SavingsChart";
import { bundles, isAdmin, jobs, org, outcomePending, reports, workload } from "@/lib/data";
import { formatUsd, parseDecimal } from "@/lib/decimal";
import { enforceGate } from "@/lib/enforce";
import { percent, rateWithInterval, utc } from "@/lib/format";
import { bars, daily, latestWeek } from "@/lib/reports";

function metric(metrics: Record<string, unknown> | null, key: string): string {
  const value = metrics?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function rate(metrics: Record<string, unknown> | null, key: string): string {
  const value = metrics?.[key];
  return typeof value === "string" ? percent(Number(value)) : "—";
}

export default async function WorkloadPage({
  params,
}: {
  params: Promise<{ org: string; id: string }>;
}) {
  const { org: orgId, id } = await params;
  const o = await org(orgId);
  const w = await workload(o.id, id);
  const [history, reportList, recentJobs, pending] = await Promise.all([
    bundles(o.id, [w.id]),
    reports(o.id, [w.id], 60),
    jobs(o.id, w.id),
    outcomePending(o.id, w.id),
  ]);
  const admin = isAdmin(o);
  const member = o.role !== "viewer";
  const week = latestWeek(reportList);
  const lostTotal = daily(reportList).reduce((n, r) => n + r.successes_lost, 0);
  const gate = enforceGate(history, reportList);
  const hidden = { org: o.id, workload: w.id };

  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        <Link href={`/o/${o.id}/workloads`}>Workloads</Link> /
      </p>
      <h1>{w.name}</h1>
      <p>
        <span className={`badge ${w.mode}`} data-testid="mode">
          {w.mode}
        </span>{" "}
        {w.override ? (
          <span className="badge override" data-testid="override">
            override: shadow
          </span>
        ) : null}{" "}
        <span className="muted">
          target recall {w.target_recall} · δ {w.delta} · min steps {w.min_steps} · holdout{" "}
          {w.holdout_rate}
        </span>
      </p>

      <div className="grid">
        <div className="panel stat">
          <div className="label">Verified savings, 7 days</div>
          <div className="value">{week ? formatUsd(parseDecimal(week.verified_saving_usd)) : "—"}</div>
        </div>
        <div className="panel stat">
          <div className="label">Estimated savings, 7 days</div>
          <div className="value">{week ? formatUsd(parseDecimal(week.estimated_saving_usd)) : "—"}</div>
        </div>
        <div className="panel stat">
          <div className="label">Successes lost, 7 days (60 days)</div>
          <div className="value">
            {week ? String(week.successes_lost) : "—"}{" "}
            <span className="muted">({String(lostTotal)})</span>
          </div>
        </div>
        <div className="panel stat">
          <div className="label">False-abort rate, 7 days (95% CI)</div>
          <div className="value">
            {week ? rateWithInterval(week.false_abort_rate, week.fa_ci_low, week.fa_ci_high) : "—"}
          </div>
        </div>
        <div className="panel stat">
          <div className="label">Outcome pending</div>
          <div className="value" data-testid="outcome-pending">
            {String(pending)}
          </div>
          <div className="label">Ended runs without an outcome; not used until one arrives.</div>
        </div>
      </div>

      <h2>Savings per day</h2>
      <div className="panel">
        <SavingsChart data={bars(reportList)} />
      </div>

      <h2>Controls</h2>
      <div className="row">
        <div className="panel" style={{ flex: "1 1 280px" }}>
          <h3 style={{ marginTop: 0 }}>Calibration</h3>
          <p className="muted">Train and validate a new bundle on this workload’s labelled runs.</p>
          <ActionForm action={calibrate} submit="Calibrate" hidden={hidden} disabled={!member} />
        </div>
        <div className="panel" style={{ flex: "1 1 280px" }}>
          <h3 style={{ marginTop: 0 }}>Mode</h3>
          {w.mode === "shadow" ? (
            <>
              <p className="muted">
                In shadow, SDKs record what they would abort but never abort.
              </p>
              <ActionForm
                action={setMode}
                submit="Switch to enforce"
                hidden={{ ...hidden, mode: "enforce" }}
                disabled={!admin || !gate.allowed}
                confirm="Switch to enforce? SDKs will start aborting runs at their next refresh."
              />
              {gate.reasons.map((r) => (
                <p key={r} className="note muted" data-testid="enforce-blocked">
                  {r}
                </p>
              ))}
            </>
          ) : (
            <>
              <p className="muted">Enforcing: SDKs abort runs the bundle flags (except holdout).</p>
              <ActionForm
                action={setMode}
                submit="Switch to shadow"
                hidden={{ ...hidden, mode: "shadow" }}
                disabled={!admin}
                variant="secondary"
              />
            </>
          )}
        </div>
        <div className="panel" style={{ flex: "1 1 280px" }}>
          <h3 style={{ marginTop: 0 }}>Emergency override</h3>
          {w.override ? (
            <>
              <p className="muted">SDKs are held in shadow regardless of the signed mode.</p>
              <ActionForm
                action={setOverride}
                submit="Clear override"
                hidden={{ ...hidden, override: "" }}
                disabled={!admin}
                variant="secondary"
              />
            </>
          ) : (
            <>
              <p className="muted">Stop all aborts now. Takes effect within 5 minutes.</p>
              <ActionForm
                action={setOverride}
                submit="Override to shadow"
                hidden={{ ...hidden, override: "shadow" }}
                disabled={!member}
                variant="danger"
                confirm="Stop all aborts for this workload now?"
              />
            </>
          )}
        </div>
      </div>

      <h2>Bundles</h2>
      {history.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Created</th>
                <th>Training runs</th>
                <th>AUROC</th>
                <th>Test false-abort rate</th>
                <th>Test savings share</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {history.map((b) => (
                <tr key={b.id} data-testid={`bundle-v${String(b.version)}`}>
                  <td>v{b.version}</td>
                  <td>
                    <span className={`badge ${b.status}`}>{b.status}</span>
                    {b.reason ? <div className="muted">{b.reason}</div> : null}
                  </td>
                  <td>{utc(b.created_at)}</td>
                  <td>{metric(b.metrics, "n_train")}</td>
                  <td>{metric(b.metrics, "auroc")}</td>
                  <td>{rate(b.metrics, "test_false_abort_rate")}</td>
                  <td>{rate(b.metrics, "test_savings_share")}</td>
                  <td>
                    {b.status === "validated" && admin ? (
                      <ActionForm
                        action={activateBundle}
                        submit="Activate"
                        hidden={{ org: o.id, bundle: b.id }}
                      />
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No bundles yet.</p>
      )}

      <h2>Recent jobs</h2>
      {recentJobs.length ? (
        <ul>
          {recentJobs.map((j) => (
            <li key={j.id}>
              <code>{j.type}</code> <span className="badge">{j.status}</span>{" "}
              <span className="muted">{utc(j.created_at)}</span>
              {j.error ? <span className="error"> {j.error}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None.</p>
      )}

      <p>
        <Link href={`/o/${o.id}/workloads/${w.id}/runs`}>Explore runs →</Link>
      </p>
    </>
  );
}
