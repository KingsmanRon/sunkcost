"""G6 (complete, P4): API + worker + local Supabase, end to end.

1. Synthetic shadow runs are ingested through the API.
2. A calibration requested through the API is run by the worker and gives a validated, signed
   bundle.
3. It is activated, then switched to enforce; the worker signs the new version.
4. The SDK picks the bundle up over HTTP. A replayed failing run is aborted at exactly the step
   the model predicts, and a holdout run never is.
5. A report over known runs equals the hand-computed §7.4 figures.
6. Drift downgrades the workload, audits it, and sends a signed webhook.

The worker code runs in-process as role ``sunkcost_worker`` against the real database. Only the
webhook receiver is in-process (an httpx transport), so no outbound network is needed.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

import sunkcost.client
from sunkcost import Sunkcost
from sunkcost._ids import uuid7
from sunkcost._money import money_str
from sunkcost.core import features_v1, format_timestamp_us, is_holdout
from sunkcost.testing import TEST_SEED, TEST_TRUSTED_KEYS
from sunkcost_worker.calibration import RunData, cp_interval
from sunkcost_worker.service.db import WorkerDb
from sunkcost_worker.service.jobs import Worker
from sunkcost_worker.service.signing import Signer
from sunkcost_worker.service.webhooks import verify
from sunkcost_worker.synthetic import WorkloadProcess, generate_history, generate_run

pytestmark = pytest.mark.integration

WORKER_DB_URL = "postgresql://sunkcost_worker:sunkcost_worker_local@127.0.0.1:54322/postgres"
SEED_B64 = "nWGxne/9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A="  # RFC 8032 test key, local only
assert __import__("base64").b64decode(SEED_B64) == TEST_SEED


class Receiver:
    """In-process webhook endpoint: records requests and answers 204."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(204)


@pytest.fixture
def receiver() -> Receiver:
    return Receiver()


@pytest.fixture
def worker(receiver: Receiver) -> Any:
    db = WorkerDb(WORKER_DB_URL)
    w = Worker(
        db,
        Signer(SEED_B64, "rfc8032-test-1"),
        webhook_allow_private=True,
        webhook_transport=httpx.MockTransport(receiver),
    )
    w.publish_signing_key()
    yield w
    db.close()


def ingest(api: str, key: str, events: list[dict[str, Any]]) -> None:
    for i in range(0, len(events), 1000):
        body = {
            "schema_version": "1",
            "sent_at": format_timestamp_us(time.time_ns() // 1000),
            "sdk": {"name": "sunkcost-python", "version": "0.1.0"},
            "dropped_events": 0,
            "events": events[i : i + 1000],
        }
        r = httpx.post(
            f"{api}/v1/ingest", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120
        )
        assert r.status_code == 200, r.text
        assert r.json()["rejected"] == [], r.json()["rejected"][:3]


def run_events(run: RunData, workload: str, **run_end: Any) -> list[dict[str, Any]]:
    rid = run.run_id
    events: list[dict[str, Any]] = [
        {
            "type": "run_start",
            "run_id": rid,
            "workload": workload,
            "external_ref": None,
            "started_at": format_timestamp_us(run.started_at_us),
            "bundle_id": run_end.pop("bundle_id", None),
            "bundle_version": run_end.pop("bundle_version", None),
            "mode": run_end.pop("mode", "shadow"),
            "holdout": run_end.pop("holdout", False),
        }
    ]
    for seq, (s, cost) in enumerate(zip(run.steps, run.costs, strict=True), start=1):
        events.append(
            {
                "type": "step",
                "run_id": rid,
                "seq": seq,
                "ts": format_timestamp_us(s.ts_us),
                "kind": s.kind,
                "model": s.model,
                "tool_name": s.tool_name,
                "args_hash": s.args_hash,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cost_usd": money_str(cost),
                "error": s.error,
                "latency_ms": None,
                "decision": None,
            }
        )
    end = {
        "would_abort_step": None,
        "cost_through_would_abort_usd": None,
        "aborted_step": None,
        **run_end,
    }
    events.append(
        {
            "type": "run_end",
            "run_id": rid,
            "ended_at": format_timestamp_us(run.steps[-1].ts_us + 1000),
            "status": "aborted" if end["aborted_step"] is not None else "completed",
            "total_cost_usd": money_str(run.total_cost),
            "steps": run.n_steps,
            **end,
        }
    )
    if run.outcome is not None:
        events.append(
            {
                "type": "outcome",
                "run_id": rid,
                "outcome": run.outcome,
                "source": "test",
                "reported_at": format_timestamp_us(time.time_ns() // 1000),
            }
        )
    return events


def run(worker: Any, org_id: str, job_id: str) -> None:
    """Run this test's own job. The local database keeps other tests' due jobs, and
    ``run_due_jobs()`` handles at most 20 per pass, so it may not reach this one."""
    assert worker.run_job(org_id, str(job_id))


def job_result(db_admin: Any, job_id: str) -> dict[str, Any]:
    row = db_admin("select status, result, error from public.jobs where id = %s", (job_id,))[0]
    assert row["status"] == "done", row
    return dict(row["result"])


def predicted_abort(run: RunData, bundle: Any) -> int | None:
    """The first prefix k at which check() would see score >= threshold."""
    for k in range(bundle.min_steps, run.n_steps):
        if bundle.model.score(features_v1(run.started_at_us, run.steps[:k])) >= bundle.threshold:
            return k
    return None


class Clock:
    t = 0


def replay(
    sl: Sunkcost, workload: str, run: RunData, run_id: str, clock: Clock
) -> tuple[Any, list[Any]]:
    """Replay a synthetic run through the SDK with its recorded timestamps."""
    clock.t = run.started_at_us
    sdk_run = sl.start_run(workload, run_id=run_id)
    decisions = []
    for s, cost in zip(run.steps, run.costs, strict=True):
        d = sdk_run.check()
        decisions.append(d)
        if d.abort:
            sdk_run.end("aborted")
            return sdk_run, decisions
        clock.t = s.ts_us
        sdk_run.record_step(
            s.kind,
            model=s.model,
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cost_usd=cost,
            tool_name=s.tool_name,
            tool_args=s.args_hash,
            error=s.error,
        )
    sdk_run.end("completed")
    return sdk_run, decisions


def test_calibration_to_enforcement_with_holdout(
    api: str, tenant: Any, worker: Any, db_admin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload = "ci.fix_tests"
    process = WorkloadProcess()
    history = generate_history(random.Random(1), process, 1000)
    ingest(api, tenant.key, [e for run in history for e in run_events(run, workload)])
    wid = str(
        db_admin(
            "select id from public.workloads where org_id = %s and name = %s",
            (tenant.org_id, workload),
        )[0]["id"]
    )
    user = tenant.account.headers(tenant.org_id)

    # 2. Calibrate (API enqueues, worker runs) → a validated, signed bundle.
    job = httpx.post(f"{api}/v1/workloads/{wid}/calibrate", headers=user, timeout=10).json()[
        "job_id"
    ]
    run(worker, tenant.org_id, job)
    result = job_result(db_admin, job)
    assert (result["status"], result["runs_used"], result["runs_incomplete"]) == (
        "validated",
        1000,
        0,
    )
    bundle_row = db_admin(
        "select id, version, status, signature, payload from public.policy_bundles where id = %s",
        (result["bundle_id"],),
    )[0]
    assert (bundle_row["status"], bundle_row["version"], bundle_row["payload"]["mode"]) == (
        "validated",
        1,
        "shadow",
    )

    # 3. Activate, then switch to enforce: the worker issues a new signed version.
    assert (
        httpx.post(
            f"{api}/v1/bundles/{bundle_row['id']}/activate", headers=user, timeout=10
        ).status_code
        == 200
    )
    r = httpx.patch(f"{api}/v1/workloads/{wid}", json={"mode": "enforce"}, headers=user, timeout=10)
    resign = r.json()["resign_job_id"]
    run(worker, tenant.org_id, resign)
    resigned = job_result(db_admin, resign)
    assert (resigned["status"], resigned["version"], resigned["mode"]) == (
        "new_version",
        2,
        "enforce",
    )

    # 4. The SDK fetches and verifies it over HTTP; replay failing runs with recorded timestamps.
    clock = Clock()
    sl = Sunkcost(
        api_key=tenant.key,
        endpoint=api,
        trusted_keys=TEST_TRUSTED_KEYS,
        args_hash_salt="g6",
        workloads=[workload],
        flush_interval_s=0.1,
    )
    sl.refresh_bundles()
    bundle = sl.bundles.get(workload)[0]
    assert bundle is not None
    assert (bundle.version, bundle.mode, bundle.holdout_rate) == (2, "enforce", "0.05")
    monkeypatch.setattr(sunkcost.client, "_now_us", lambda: clock.t)

    rng = random.Random(99)
    target, k = None, None
    while k is None:
        target = generate_run(rng, process, history[-1].started_at_us + 10**9, outcome="failure")
        k = predicted_abort(target, bundle)
    assert target is not None
    normal = next(i for i in iter(uuid7, None) if not is_holdout(i, "0.05"))
    held = next(i for i in iter(uuid7, None) if is_holdout(i, "0.05"))

    run_a, decisions_a = replay(sl, workload, target, normal, clock)
    assert [d.reason for d in decisions_a][-1] == "above_threshold"
    assert len(decisions_a) == k + 1  # aborted before step k + 1, exactly as predicted
    run_b, decisions_b = replay(sl, workload, target, held, clock)
    assert not any(d.abort for d in decisions_b)
    assert decisions_b[k].reason == "holdout"
    monkeypatch.undo()
    assert sl.flush(30)
    sl.close()
    a = db_admin(
        "select status, mode, holdout, aborted_step, would_abort_step, bundle_version "
        "from public.runs "
        "where org_id = %s and id = %s",
        (tenant.org_id, run_a.run_id),
    )[0]
    b = db_admin(
        "select status, holdout, aborted_step, would_abort_step from public.runs "
        "where org_id = %s and id = %s",
        (tenant.org_id, run_b.run_id),
    )[0]
    assert (
        a["status"],
        a["mode"],
        a["holdout"],
        a["aborted_step"],
        a["would_abort_step"],
        a["bundle_version"],
    ) == ("aborted", "enforce", False, k, k, 2)
    assert (b["status"], b["holdout"], b["aborted_step"], b["would_abort_step"]) == (
        "completed",
        True,
        None,
        k,
    )


def _run(started_us: int, costs: list[str], outcome: str | None) -> RunData:
    from sunkcost.core import StepRecord

    steps = tuple(
        StepRecord("tool", None, "t", None, 0, 0, float(c), False, started_us + (i + 1) * 1000)
        for i, c in enumerate(costs)
    )
    return RunData(
        run_id=uuid7(started_us // 1000),
        started_at_us=started_us,
        steps=steps,
        costs=tuple(Decimal(c) for c in costs),
        outcome=outcome,  # type: ignore[arg-type]  # "success" | "failure" | None in these tests
    )


def test_report_equals_hand_computed_figures(
    api: str, tenant: Any, worker: Any, db_admin: Any
) -> None:
    workload = "ops.report"
    day_us = 1_792_454_400_000_000  # 2026-10-20T00:00:00Z
    t = day_us + 3_600_000_000
    # A retired bundle whose test split says aborted failures saved 0.25 on average.
    ingest(api, tenant.key, run_events(_run(t, ["0.1"], "success"), workload))
    wid = str(
        db_admin(
            "select id from public.workloads where org_id = %s and name = %s",
            (tenant.org_id, workload),
        )[0]["id"]
    )
    bundle_id = uuid7()
    db_admin(
        "insert into public.policy_bundles "
        "(id, org_id, workload_id, version, status, payload, signature, "
        "key_id, metrics) values (%s, %s, %s, 1, 'retired', '{}', 's', 'k', %s)",
        (bundle_id, tenant.org_id, wid, json.dumps({"test_mean_saved_cost_usd": "0.25"})),
    )
    fail_shadow = _run(t + 1, ["0.3", "0.2", "0.5"], "failure")  # would abort at 1: saves 0.7
    lost = _run(t + 2, ["0.1", "0.1", "0.1", "0.1"], "success")  # would abort at 3: a lost success
    held_fail = _run(t + 3, ["0.1", "0.4"], "failure")  # holdout, would abort at 1: saves 0.4
    aborted = _run(t + 4, ["0.2", "0.2"], "failure")  # enforced abort: estimated 0.25
    pending = _run(t + 5, ["0.9", "0.9"], None)  # no outcome yet
    events = (
        run_events(fail_shadow, workload, would_abort_step=1, cost_through_would_abort_usd="0.3")
        + run_events(lost, workload, would_abort_step=3, cost_through_would_abort_usd="0.3")
        + run_events(
            held_fail,
            workload,
            mode="enforce",
            holdout=True,
            bundle_id=bundle_id,
            bundle_version=1,
            would_abort_step=1,
            cost_through_would_abort_usd="0.1",
        )
        + run_events(
            aborted,
            workload,
            mode="enforce",
            holdout=False,
            bundle_id=bundle_id,
            bundle_version=1,
            would_abort_step=2,
            cost_through_would_abort_usd="0.4",
            aborted_step=2,
        )
        + run_events(pending, workload)
        + run_events(
            _run(day_us + 86_400_000_000 + 5, ["5"], "failure"), workload
        )  # next day: excluded
    )
    ingest(api, tenant.key, events)
    job = db_admin(
        "insert into public.jobs (org_id, type, payload) "
        "values (%s, 'build_report', %s) returning id",
        (
            tenant.org_id,
            json.dumps(
                {
                    "workload_id": wid,
                    "period_start": "2026-10-20T00:00:00Z",
                    "period_end": "2026-10-21T00:00:00Z",
                }
            ),
        ),
    )[0]["id"]
    run(worker, tenant.org_id, job)
    assert job_result(db_admin, str(job))["outcome_pending"] == 1
    rep = db_admin("select * from public.reports where workload_id = %s", (wid,))[0]
    # By hand: 6 runs in the period. Verified 0.7 + 0.4 = 1.1 from two caught failures; estimated
    # 0.25 from the one enforced abort; observed successes 2 (one lost), so the rate is 1/2.
    low, high = cp_interval(1, 2, 0.95)
    assert rep["runs"] == 6
    assert str(rep["verified_saving_usd"]) == "1.100000000"
    assert str(rep["estimated_saving_usd"]) == "0.250000000"
    assert (rep["failures_caught"], rep["successes_lost"], rep["false_abort_rate"]) == (2, 1, 0.5)
    assert (rep["fa_ci_low"], rep["fa_ci_high"]) == (low, high)
    # Two runs were enforced (one held out); the first run started at 01:00 (ADR-0007).
    assert rep["enforced_runs"] == 2
    assert rep["first_run_at"].isoformat() == "2026-10-20T01:00:00+00:00"


def test_drift_downgrades_audits_and_notifies(
    api: str, tenant: Any, worker: Any, db_admin: Any, receiver: Receiver
) -> None:
    workload = "drift.case"
    user = tenant.account.headers(tenant.org_id)
    hook = httpx.put(
        f"{api}/v1/orgs/webhook",
        json={"url": "https://hooks.example.test/sunkcost"},
        headers=user,
        timeout=10,
    ).json()
    t = time.time_ns() // 1000 - 3_600_000_000
    ingest(api, tenant.key, run_events(_run(t, ["0.1"], "success"), workload))
    wid = str(
        db_admin(
            "select id from public.workloads where org_id = %s and name = %s",
            (tenant.org_id, workload),
        )[0]["id"]
    )
    from sunkcost.testing import make_bundle, sign

    doc = make_bundle(workload, issued_at_us=time.time_ns() // 1000, mode="enforce")
    signed = sign(doc)
    db_admin(
        "insert into public.policy_bundles "
        "(id, org_id, workload_id, version, status, payload, signature, "
        "key_id, activated_at) values (%s, %s, %s, 1, 'active', %s, %s, 'rfc8032-test-1', now())",
        (doc["bundle_id"], tenant.org_id, wid, json.dumps(doc), signed["signature"]),
    )
    # 40 successful shadow runs on this bundle, 30 of which it would have aborted (α = 0.05).
    events: list[dict[str, Any]] = []
    for i in range(40):
        events += run_events(
            _run(t + i, ["0.1", "0.1"], "success"),
            workload,
            bundle_id=doc["bundle_id"],
            bundle_version=1,
            **({"would_abort_step": 1, "cost_through_would_abort_usd": "0.1"} if i < 30 else {}),
        )
    ingest(api, tenant.key, events)
    job = db_admin(
        "insert into public.jobs (org_id, type, payload) "
        "values (%s, 'drift_check', %s) returning id",
        (tenant.org_id, json.dumps({"workload_id": wid})),
    )[0]["id"]
    run(worker, tenant.org_id, job)  # drift_check, which enqueues the webhook
    (webhook,) = db_admin(
        "select id from public.jobs where org_id = %s and type = 'webhook'", (tenant.org_id,)
    )
    run(worker, tenant.org_id, webhook["id"])
    result = job_result(db_admin, str(job))
    assert (result["n"], result["k"], result["downgraded"]) == (40, 30, True)
    assert (
        db_admin("select override from public.workloads where id = %s", (wid,))[0]["override"]
        == "shadow"
    )
    audit = db_admin(
        "select actor, after from public.audit_log "
        "where org_id = %s and action = 'workload.override'",
        (tenant.org_id,),
    )
    assert audit[-1]["actor"] == "system"
    assert audit[-1]["after"]["reason"] == "drift"
    (request,) = receiver.requests
    assert verify(hook["secret"], request.headers["x-sunkcost-signature"], request.content)
    assert json.loads(request.content)["type"] == "workload.downgraded"
    # The SDK sees the override at its next refresh and stops aborting.
    r = httpx.get(
        f"{api}/v1/bundles/active",
        params={"workload": workload},
        headers={"Authorization": f"Bearer {tenant.key}"},
        timeout=10,
    )
    assert r.json()["override"] == "shadow"


def test_scheduler_enqueues_once_per_period(
    api: str, tenant: Any, worker: Any, db_admin: Any
) -> None:
    ingest(
        api, tenant.key, run_events(_run(time.time_ns() // 1000, ["0.1"], "success"), "sched.case")
    )
    first = worker.schedule()
    assert first >= 2  # at least this workload's report and this org's retention
    assert worker.schedule() == 0  # idempotent within the period
    kinds = {
        r["type"]
        for r in db_admin("select type from public.jobs where org_id = %s", (tenant.org_id,))
    }
    assert {"build_report", "retention"} <= kinds
    periods = {
        (r["payload"]["period_end"], r["payload"]["period_start"])
        for r in db_admin(
            "select payload from public.jobs where org_id = %s and type = 'build_report'",
            (tenant.org_id,),
        )
    }
    lengths = {datetime.fromisoformat(e) - datetime.fromisoformat(b) for e, b in periods}
    assert lengths == {timedelta(days=1), timedelta(days=7)}  # daily and trailing week (ADR-0007)
    # The runner loop drains the queue, other tests' leftovers included.
    assert worker.run_due_jobs(limit=10_000) >= 2
    statuses = db_admin(
        "select type, status from public.jobs where org_id = %s and type = any(%s)",
        (tenant.org_id, ["build_report", "retention"]),
    )
    assert {r["status"] for r in statuses} == {"done"}


def test_failing_jobs_retry_then_die(tenant: Any, worker: Any, db_admin: Any) -> None:
    job = str(
        db_admin(
            "insert into public.jobs (org_id, type, payload) "
            "values (%s, 'calibrate', %s) returning id",
            (tenant.org_id, json.dumps({"workload_id": uuid7()})),
        )[0]["id"]
    )
    for attempt in (1, 2, 3):
        assert worker.run_job(tenant.org_id, job)
        row = db_admin("select status, attempts, error from public.jobs where id = %s", (job,))[0]
        assert row["attempts"] == attempt
        assert "workload not found" in row["error"]
        db_admin("update public.jobs set run_after = now() where id = %s", (job,))
    assert row["status"] == "dead"
    assert not worker.run_job(tenant.org_id, job)  # dead jobs are not claimed again


def test_worker_publishes_its_signing_key(api: str, worker: Any) -> None:
    keys = httpx.get(f"{api}/v1/signing-keys", timeout=10).json()["keys"]
    assert {"key_id": "rfc8032-test-1"}.items() <= next(
        k for k in keys if k["key_id"] == "rfc8032-test-1"
    ).items()


def test_calibration_without_signal_is_recorded_as_rejected(
    api: str, tenant: Any, worker: Any, db_admin: Any
) -> None:
    workload = "noise.case"
    process = WorkloadProcess(p_failure=0.35, signal=0.0)  # failures look exactly like successes
    history = generate_history(random.Random(7), process, 900)
    ingest(api, tenant.key, [e for run in history for e in run_events(run, workload)])
    wid = str(
        db_admin(
            "select id from public.workloads where org_id = %s and name = %s",
            (tenant.org_id, workload),
        )[0]["id"]
    )
    user = tenant.account.headers(tenant.org_id)
    job = httpx.post(f"{api}/v1/workloads/{wid}/calibrate", headers=user, timeout=10).json()[
        "job_id"
    ]
    run(worker, tenant.org_id, job)
    result = job_result(db_admin, job)
    assert result["status"] == "rejected"
    row = db_admin(
        "select status, signature, reason from public.policy_bundles where id = %s",
        (result["bundle_id"],),
    )[0]
    assert (row["status"], row["signature"]) == ("rejected", None)
    assert row["reason"]
    activate = httpx.post(
        f"{api}/v1/bundles/{result['bundle_id']}/activate", headers=user, timeout=10
    )
    assert activate.status_code == 409  # a rejected bundle can never be activated
