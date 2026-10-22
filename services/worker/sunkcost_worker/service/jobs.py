"""Job handlers, the claim/finish cycle, and the scheduler (ARCHITECTURE.md §4 C7, §5.3, §5.4).

Jobs are claimed one at a time with ``FOR UPDATE SKIP LOCKED``. A job gets 3 attempts with
backoff (webhooks get 5 over about an hour), then becomes ``dead``. A job still ``running`` 15
minutes after it was claimed is due again, because its worker died. Heavy work (training) happens
outside any transaction: data is read in one transaction and results written in another.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from sunkcost._ids import uuid7
from sunkcost.core import StepRecord, datetime_to_us
from sunkcost_worker.calibration import (
    AccountedRun,
    CalibrationSettings,
    RunData,
    account,
    calibrate,
    cp_lower,
)
from sunkcost_worker.service import webhooks
from sunkcost_worker.service.bundles import bundle_document, rate_str, renewed
from sunkcost_worker.service.db import Conn, WorkerDb, one, required, rows
from sunkcost_worker.service.signing import Signer

log = logging.getLogger("sunkcost.worker")

MAX_ATTEMPTS = 3
BACKOFF_S = (30, 120)  # before attempts 2 and 3
MAX_CALIBRATION_RUNS = 50_000
NIGHTLY_MIN_NEW_RUNS = 200
RETENTION_DAYS = 90
DRIFT_WINDOW_DAYS = 7
RESIGN_ALERT_HOURS = 36
SCHEDULER_LOCK = 0x5C0571  # pg_advisory_lock key held by the one scheduling worker


class JobFailed(Exception):  # noqa: N818 - a job outcome, raised to record it
    """A job that cannot succeed now; it is retried until its attempts are used up."""


def _now_us() -> int:
    return time.time_ns() // 1000


def _dt(us: int) -> datetime:
    return datetime.fromtimestamp(us / 1e6, tz=timezone.utc)


# -- SQL ------------------------------------------------------------------------------------------

WORKLOAD_SQL = (
    "select id, org_id, project_id, name, mode, override, target_recall, delta, min_steps, "
    "holdout_rate, min_savings_share from public.workloads where id = %s"
)
CALIBRATION_RUNS_SQL = (
    "select id, started_at, outcome, mode, holdout, aborted_step, steps from public.runs "
    "where org_id = %s and workload_id = %s and outcome in ('success', 'failure') "
    "and started_at is not null and end_status is not null and aborted_step is null "
    "and (mode = 'shadow' or holdout) order by started_at desc limit %s"
)
STEPS_SQL = (
    "select run_id, seq, ts, kind, model, tool_name, args_hash, input_tokens, output_tokens, "
    "cost_usd, error from public.step_events where org_id = %s and run_id = any(%s) "
    "order by run_id, seq"
)
NEXT_VERSION_SQL = (
    "select coalesce(max(version), 0) + 1 as version from public.policy_bundles "
    "where workload_id = %s"
)
INSERT_BUNDLE_SQL = (
    "insert into public.policy_bundles (id, org_id, workload_id, version, status, payload, "
    "signature, key_id, metrics, reason, activated_at) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id"
)
ACTIVE_SQL = (
    "select id, version, payload, metrics from public.policy_bundles "
    "where workload_id = %s and status = 'active' for update"
)
REPORT_RUNS_SQL = (
    "select mode, holdout, outcome, total_cost_usd, would_abort_step, cost_through_would_abort, "
    "aborted_step, bundle_id from public.runs where org_id = %s and workload_id = %s "
    "and started_at >= %s and started_at < %s"
)
MEAN_SAVING_SQL = (
    "select id, metrics ->> 'test_mean_saved_cost_usd' as mean from public.policy_bundles "
    "where workload_id = %s and metrics ? 'test_mean_saved_cost_usd'"
)
UPSERT_REPORT_SQL = (
    "insert into public.reports (org_id, workload_id, period_start, period_end, runs, "
    "verified_saving_usd, estimated_saving_usd, failures_caught, successes_lost, false_abort_rate, "
    "fa_ci_low, fa_ci_high) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "on conflict (workload_id, period_start, period_end) do update set runs = excluded.runs, "
    "verified_saving_usd = excluded.verified_saving_usd, "
    "estimated_saving_usd = excluded.estimated_saving_usd, "
    "failures_caught = excluded.failures_caught, successes_lost = excluded.successes_lost, "
    "false_abort_rate = excluded.false_abort_rate, fa_ci_low = excluded.fa_ci_low, "
    "fa_ci_high = excluded.fa_ci_high returning id"
)
DRIFT_RUNS_SQL = (
    "select count(*) as n, count(would_abort_step) as k from public.runs "
    "where org_id = %s and workload_id = %s and bundle_id = %s and outcome = 'success' "
    "and aborted_step is null and (mode = 'shadow' or holdout) and started_at >= %s"
)
CLAIM_SQL = (
    "with j as (select id from public.jobs where id = %s and ("
    "(status in ('queued', 'failed') and run_after <= now()) "
    "or (status = 'running' and locked_at < now() - interval '15 minutes')) "
    "for update skip locked) "
    "update public.jobs set status = 'running', attempts = attempts + 1, locked_at = now(), "
    "updated_at = now() from j where jobs.id = j.id "
    "returning jobs.id, jobs.type, jobs.payload, jobs.attempts"
)
ENQUEUE_SQL = (
    "insert into public.jobs (org_id, type, payload, dedupe_key) values (%s, %s, %s, %s) "
    "on conflict (dedupe_key) where dedupe_key is not null do nothing returning id"
)
AUDIT_SQL = (
    "insert into public.audit_log (org_id, actor, action, target, before, after) "
    "values (%s, 'system', %s, %s, %s, %s)"
)


def enqueue(
    conn: Conn, org_id: str, job_type: str, payload: dict[str, Any], dedupe_key: str | None = None
) -> str | None:
    row = one(conn, ENQUEUE_SQL, (org_id, job_type, Jsonb(payload), dedupe_key))
    return None if row is None else str(row["id"])


def audit(
    conn: Conn, org_id: str, action: str, target: str, before: Any = None, after: Any = None
) -> None:
    conn.execute(
        AUDIT_SQL,
        (
            org_id,
            action,
            target,
            None if before is None else Jsonb(before),
            None if after is None else Jsonb(after),
        ),
    )


class Worker:
    def __init__(
        self,
        db: WorkerDb,
        signer: Signer,
        *,
        webhook_timeout_s: float = 10.0,
        webhook_allow_private: bool = False,
        webhook_transport: httpx.BaseTransport | None = None,
        clock_us: Callable[[], int] = _now_us,
    ) -> None:
        self.db = db
        self.signer = signer
        self.webhook_timeout_s = webhook_timeout_s
        self.webhook_allow_private = webhook_allow_private
        self.webhook_transport = webhook_transport
        self.clock_us = clock_us
        self.handlers: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
            "calibrate": self.calibrate,
            "resign_bundle": self.resign_bundle,
            "build_report": self.build_report,
            "drift_check": self.drift_check,
            "retention": self.retention,
            "webhook": self.webhook,
        }
        self.last_poll_us = 0
        self.is_scheduler = False

    # -- start-up ----------------------------------------------------------------------------------

    def publish_signing_key(self) -> None:
        """Serve this worker's public key at /v1/signing-keys (rotation: §9)."""
        with self.db.tx() as conn:
            conn.execute(
                "insert into public.signing_keys (key_id, public_key_b64) values (%s, %s) "
                "on conflict (key_id) do update set public_key_b64 = excluded.public_key_b64, "
                "retired_at = null",
                (self.signer.key_id, self.signer.public_key_b64),
            )

    # -- the job cycle -----------------------------------------------------------------------------

    def due(self) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            return rows(conn, "select * from public.list_due_work()")

    def run_due_jobs(self, limit: int = 20) -> int:
        """Claim and run due jobs; returns how many ran."""
        self.last_poll_us = self.clock_us()
        ran = 0
        for item in self.due():
            if item["kind"] != "job":
                continue
            if self.run_job(str(item["org_id"]), str(item["id"])):
                ran += 1
            if ran >= limit:
                break
        return ran

    def run_job(self, org_id: str, job_id: str) -> bool:
        with self.db.tx(org_id) as conn:
            job = one(conn, CLAIM_SQL, (job_id,))
        if job is None:
            return False  # another worker has it, or it is no longer due
        handler = self.handlers.get(job["type"])
        started = time.monotonic()
        try:
            if handler is None:
                raise JobFailed(f"unknown job type {job['type']!r}")
            result = handler(org_id, dict(job["payload"]))
        except Exception as exc:  # every failure is recorded on the job, never lost
            self._fail(org_id, job, exc)
            return True
        with self.db.tx(org_id) as conn:
            conn.execute(
                "update public.jobs set status = 'done', result = %s, error = null, "
                "locked_at = null, "
                "finished_at = now(), updated_at = now() where id = %s",
                (Jsonb(result), job_id),
            )
        log.info(
            "job done",
            extra={
                "job_id": job_id,
                "type": job["type"],
                "s": round(time.monotonic() - started, 2),
            },
        )
        return True

    def _fail(self, org_id: str, job: dict[str, Any], exc: Exception) -> None:
        attempts = int(job["attempts"])
        limit = webhooks.MAX_ATTEMPTS if job["type"] == "webhook" else MAX_ATTEMPTS
        delays = webhooks.RETRY_DELAYS_S if job["type"] == "webhook" else BACKOFF_S
        message = f"{type(exc).__name__}: {exc}"[:2000]
        dead = attempts >= limit
        delay = 0 if dead else delays[min(attempts - 1, len(delays) - 1)]
        with self.db.tx(org_id) as conn:
            conn.execute(
                "update public.jobs set status = %s, error = %s, locked_at = null, "
                "run_after = now() + make_interval(secs => %s), updated_at = now(), "
                "finished_at = case when %s then now() end where id = %s",
                ("dead" if dead else "failed", message, delay, dead, str(job["id"])),
            )
        level = logging.ERROR if dead else logging.WARNING
        log.log(
            level,
            "job failed",
            extra={
                "job_id": str(job["id"]),
                "type": job["type"],
                "attempt": attempts,
                "dead": dead,
                "error": message,
            },
        )

    # -- calibrate (§5.3, §7) ----------------------------------------------------------------------

    def calibrate(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        wid = str(payload["workload_id"])
        with self.db.tx(org_id) as conn:
            w = one(conn, WORKLOAD_SQL, (wid,))
            if w is None:
                raise JobFailed("workload not found")
            run_rows = rows(conn, CALIBRATION_RUNS_SQL, (org_id, wid, MAX_CALIBRATION_RUNS))
            step_rows = rows(conn, STEPS_SQL, (org_id, [r["id"] for r in run_rows]))
        runs, incomplete = self._run_data(run_rows, step_rows)
        settings = CalibrationSettings(
            target_recall=rate_str(w["target_recall"]),
            delta=rate_str(w["delta"]),
            min_steps=int(w["min_steps"]),
            min_savings_share=rate_str(w["min_savings_share"]),
        )
        result = calibrate(runs, settings)  # outside any transaction: this can take minutes
        summary: dict[str, Any] = {
            "status": result.status,
            "reason": result.reason,
            "counts": result.counts,
            "runs_used": len(runs),
            "runs_incomplete": incomplete,
        }
        if result.status == "insufficient_data" or result.bundle_model is None:
            return summary
        now = self.clock_us()
        with self.db.tx(org_id) as conn:
            conn.execute("select 1 from public.workloads where id = %s for update", (wid,))
            version = int(required(conn, NEXT_VERSION_SQL, (wid,))["version"])
            decision = result.decision or {
                "threshold": "1.0",
                "min_steps": settings.min_steps,
                "target_recall": settings.target_recall,
                "delta": settings.delta,
            }
            doc = bundle_document(
                org_id=org_id,
                project_id=str(w["project_id"]),
                workload_id=wid,
                workload=w["name"],
                version=version,
                mode=w["mode"],
                holdout_rate=w["holdout_rate"],
                model=result.bundle_model,
                decision=decision,
                metrics=dict(result.metrics),
                issued_at_us=now,
                check=result.status == "validated",
            )
            validated = result.status == "validated"
            signature = self.signer.sign(doc) if validated else None
            key_id = self.signer.key_id if validated else None
            conn.execute(
                INSERT_BUNDLE_SQL,
                (
                    doc["bundle_id"],
                    org_id,
                    wid,
                    version,
                    result.status,
                    Jsonb(doc),
                    signature,
                    key_id,
                    Jsonb(doc["metrics"]),
                    result.reason,
                    None,
                ),
            )
            audit(
                conn,
                org_id,
                "bundle.calibrated",
                f"bundle:{doc['bundle_id']}",
                after={"version": version, "status": result.status, "reason": result.reason},
            )
        summary.update({"bundle_id": doc["bundle_id"], "version": version})
        return summary

    @staticmethod
    def _run_data(
        run_rows: list[dict[str, Any]], step_rows: list[dict[str, Any]]
    ) -> tuple[list[RunData], int]:
        """Runs whose stored steps are exactly 1..n (dropped events would change the features)."""
        by_run: dict[str, list[dict[str, Any]]] = {}
        for s in step_rows:
            by_run.setdefault(str(s["run_id"]), []).append(s)
        out, incomplete = [], 0
        for r in run_rows:
            steps = by_run.get(str(r["id"]), [])
            if r["steps"] is None or [s["seq"] for s in steps] != list(
                range(1, int(r["steps"]) + 1)
            ):
                incomplete += 1
                continue
            records = tuple(
                StepRecord(
                    kind=s["kind"],
                    model=s["model"],
                    tool_name=s["tool_name"],
                    args_hash=s["args_hash"],
                    input_tokens=int(s["input_tokens"]),
                    output_tokens=int(s["output_tokens"]),
                    cost=0.0 if s["cost_usd"] is None else float(s["cost_usd"]),
                    error=bool(s["error"]),
                    ts_us=datetime_to_us(s["ts"]),
                )
                for s in steps
            )
            costs = tuple(Decimal(0) if s["cost_usd"] is None else s["cost_usd"] for s in steps)
            out.append(
                RunData(
                    run_id=str(r["id"]),
                    started_at_us=datetime_to_us(r["started_at"]),
                    steps=records,
                    costs=costs,
                    outcome=r["outcome"],
                    mode=r["mode"],
                    holdout=bool(r["holdout"]),
                    aborted_step=r["aborted_step"],
                )
            )
        return out, incomplete

    # -- re-sign (§6.2) ----------------------------------------------------------------------------

    def resign_bundle(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Daily: re-sign the active bundle with a new expiry (same version). Mode change: a new
        signed version with the workload's mode replaces it, because raising enforcement always
        needs a newly signed version."""
        wid = str(payload["workload_id"])
        now = self.clock_us()
        with self.db.tx(org_id) as conn:
            w = one(conn, WORKLOAD_SQL + " for update", (wid,))
            if w is None:
                raise JobFailed("workload not found")
            active = one(conn, ACTIVE_SQL, (wid,))
            if active is None:
                return {"status": "skipped", "reason": "no active bundle"}
            doc = dict(active["payload"])
            if doc["mode"] == w["mode"]:
                new = renewed(doc, issued_at_us=now)
                conn.execute(
                    "update public.policy_bundles set payload = %s, signature = %s, key_id = %s "
                    "where id = %s",
                    (Jsonb(new), self.signer.sign(new), self.signer.key_id, str(active["id"])),
                )
                return {
                    "status": "resigned",
                    "bundle_id": str(active["id"]),
                    "expires_at": new["expires_at"],
                }
            version = int(required(conn, NEXT_VERSION_SQL, (wid,))["version"])
            new = renewed(
                doc, issued_at_us=now, bundle_id=uuid7(now // 1000), version=version, mode=w["mode"]
            )
            conn.execute(
                "update public.policy_bundles set status = 'retired' where id = %s",
                (str(active["id"]),),
            )
            conn.execute(
                INSERT_BUNDLE_SQL,
                (
                    new["bundle_id"],
                    org_id,
                    wid,
                    version,
                    "active",
                    Jsonb(new),
                    self.signer.sign(new),
                    self.signer.key_id,
                    Jsonb(active["metrics"]),
                    None,
                    _dt(now),
                ),
            )
            audit(
                conn,
                org_id,
                "bundle.mode_change",
                f"bundle:{new['bundle_id']}",
                before={"bundle_id": str(active["id"]), "mode": doc["mode"]},
                after={"bundle_id": new["bundle_id"], "mode": w["mode"], "version": version},
            )
        return {
            "status": "new_version",
            "bundle_id": new["bundle_id"],
            "version": version,
            "mode": w["mode"],
        }

    # -- reports (§7.4) ----------------------------------------------------------------------------

    def build_report(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        wid = str(payload["workload_id"])
        start, end = payload["period_start"], payload["period_end"]
        with self.db.tx(org_id) as conn:
            found = rows(conn, REPORT_RUNS_SQL, (org_id, wid, start, end))
            means = {str(r["id"]): Decimal(r["mean"]) for r in rows(conn, MEAN_SAVING_SQL, (wid,))}
            runs = [
                AccountedRun(
                    mode=r["mode"] or "shadow",
                    holdout=bool(r["holdout"]),
                    outcome=r["outcome"],
                    total_cost=r["total_cost_usd"] or Decimal(0),
                    would_abort_step=r["would_abort_step"],
                    cost_through_would_abort=r["cost_through_would_abort"],
                    aborted_step=r["aborted_step"],
                    bundle_id=None if r["bundle_id"] is None else str(r["bundle_id"]),
                )
                for r in found
            ]
            rep = account(runs, means)
            row = required(
                conn,
                UPSERT_REPORT_SQL,
                (
                    org_id,
                    wid,
                    start,
                    end,
                    rep.runs,
                    rep.verified_saving_usd,
                    rep.estimated_saving_usd,
                    rep.failures_caught,
                    rep.successes_lost,
                    rep.false_abort_rate,
                    rep.fa_ci_low,
                    rep.fa_ci_high,
                ),
            )
        return {
            "report_id": str(row["id"]),
            "runs": rep.runs,
            "verified_saving_usd": format(rep.verified_saving_usd, "f"),
            "estimated_saving_usd": format(rep.estimated_saving_usd, "f"),
            "outcome_pending": rep.outcome_pending,
        }

    # -- drift (§5.4, §7.5) ------------------------------------------------------------------------

    def drift_check(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        wid = str(payload["workload_id"])
        since = _dt(self.clock_us() - DRIFT_WINDOW_DAYS * 86_400 * 1_000_000)
        with self.db.tx(org_id) as conn:
            w = one(conn, WORKLOAD_SQL + " for update", (wid,))
            if w is None:
                raise JobFailed("workload not found")
            active = one(conn, ACTIVE_SQL, (wid,))
            if active is None:
                return {"status": "skipped", "reason": "no active bundle"}
            decision = active["payload"]["decision"]
            alpha = float(Decimal(1) - Decimal(decision["target_recall"]))
            delta = float(decision["delta"])
            counts = required(conn, DRIFT_RUNS_SQL, (org_id, wid, str(active["id"]), since))
            n, k = int(counts["n"]), int(counts["k"])
            lower = cp_lower(k, n, 1.0 - delta) if n else 0.0
            drifted = n > 0 and lower > alpha
            result = {
                "status": "ok",
                "n": n,
                "k": k,
                "lower_bound": lower,
                "alpha": alpha,
                "downgraded": False,
            }
            if drifted and w["override"] is None:
                conn.execute(
                    "update public.workloads set override = 'shadow', updated_at = now() "
                    "where id = %s",
                    (wid,),
                )
                detail = {
                    "override": "shadow",
                    "reason": "drift",
                    "n": n,
                    "k": k,
                    "false_abort_lower_bound": lower,
                    "alpha": alpha,
                }
                audit(
                    conn,
                    org_id,
                    "workload.override",
                    f"workload:{wid}",
                    before={"override": None},
                    after=detail,
                )
                event = {
                    "type": "workload.downgraded",
                    "workload_id": wid,
                    "workload": w["name"],
                    "bundle_id": str(active["id"]),
                    "false_abort_lower_bound": repr(lower),
                    "alpha": repr(alpha),
                    "at": _dt(self.clock_us()).isoformat(),
                }
                enqueue(conn, org_id, "webhook", {"event": event})
                result.update(status="drift", downgraded=True)
        return result

    # -- retention ---------------------------------------------------------------------------------

    def retention(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        days = int(payload.get("days", RETENTION_DAYS))
        with self.db.tx(org_id) as conn:
            cur = conn.execute(
                "delete from public.runs where org_id = %s "
                "and received_at < now() - make_interval(days => %s)",
                (org_id, days),
            )
        return {"runs_deleted": cur.rowcount, "older_than_days": days}

    # -- webhooks ----------------------------------------------------------------------------------

    def webhook(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.db.tx(org_id) as conn:
            target = one(conn, "select url, secret from public.org_webhook_target(%s)", (org_id,))
        if target is None:
            return {"status": "skipped", "reason": "no webhook configured"}
        try:
            status = webhooks.deliver(
                target["url"],
                target["secret"],
                payload["event"],
                timeout_s=self.webhook_timeout_s,
                allow_private=self.webhook_allow_private,
                transport=self.webhook_transport,
            )
        except webhooks.WebhookError as exc:
            raise JobFailed(str(exc)) from None
        return {"status": "delivered", "http_status": status}

    # -- scheduler (§4 C7) -------------------------------------------------------------------------

    def schedule(self) -> int:
        """One scheduler pass (call only while holding the advisory lock). Returns jobs enqueued."""
        now = _dt(self.clock_us())
        today = now.date()
        day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        yesterday = day_start - timedelta(days=1)
        with self.db.tx() as conn:
            conn.execute(
                "select public.ensure_step_partitions((now() - interval '1 month')::date, 4)"
            )
        enqueued = 0
        orgs: set[str] = set()
        for item in self.due():
            if item["kind"] != "workload":
                continue
            org_id, wid, detail = str(item["org_id"]), str(item["id"]), item["detail"]
            orgs.add(org_id)
            plans: list[tuple[str, dict[str, Any], str]] = [
                (
                    "build_report",
                    {
                        "workload_id": wid,
                        "period_start": yesterday.isoformat(),
                        "period_end": day_start.isoformat(),
                    },
                    f"report:{wid}:{yesterday:%Y%m%d}",
                ),
            ]
            if detail.get("active_bundle_id"):
                plans.append(("drift_check", {"workload_id": wid}, f"drift:{wid}:{now:%Y%m%d%H}"))
                plans.append(
                    ("resign_bundle", {"workload_id": wid}, f"resign:{wid}:{today:%Y%m%d}")
                )
                issued = detail.get("active_issued_at")
                if issued and now - datetime.fromisoformat(issued) > timedelta(
                    hours=RESIGN_ALERT_HOURS
                ):
                    log.error("active bundle not re-signed for 36 h", extra={"workload_id": wid})
            if int(detail.get("labelled_runs_since_active") or 0) >= NIGHTLY_MIN_NEW_RUNS:
                plans.append(
                    (
                        "calibrate",
                        {"workload_id": wid, "reason": "nightly"},
                        f"calibrate-nightly:{wid}:{today:%Y%m%d}",
                    )
                )
            with self.db.tx(org_id) as conn:
                for job_type, job_payload, key in plans:
                    if enqueue(conn, org_id, job_type, job_payload, key):
                        enqueued += 1
        for org_id in orgs:
            with self.db.tx(org_id) as conn:
                if enqueue(
                    conn,
                    org_id,
                    "retention",
                    {"days": RETENTION_DAYS},
                    f"retention:{org_id}:{today:%Y%m%d}",
                ):
                    enqueued += 1
        return enqueued
