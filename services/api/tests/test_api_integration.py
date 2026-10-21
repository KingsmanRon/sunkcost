"""G6 (API parts, P3): the API against a real local Supabase. Run with `make test-integration`.

Covers ingest idempotency (duplicate batches), placeholder runs, late outcomes, outcome
conflicts, bundle serving with ETags, tenant isolation through the API, and the error contract.
The worker parts of G6 (calibration → validated bundle) arrive in P4.
"""

from __future__ import annotations

import gzip
import json
import time
import uuid
from typing import Any

import httpx
import pytest

from sunkcost import Sunkcost
from sunkcost._ids import uuid7
from sunkcost.testing import TEST_PUBLIC_KEY_B64, TEST_TRUSTED_KEYS, make_bundle, sign

pytestmark = pytest.mark.integration

WORKLOAD = "support.billing_dispute"


def ts(offset_s: float = 0.0) -> str:
    us = time.time_ns() // 1000 + int(offset_s * 1e6)
    from sunkcost.core import format_timestamp_us

    return format_timestamp_us(us)


def batch(events: list[dict[str, Any]], dropped: int = 0) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "sent_at": ts(),
        "sdk": {"name": "sunkcost-python", "version": "0.1.0"},
        "dropped_events": dropped,
        "events": events,
    }


def run_start(run_id: str, *, workload: str = WORKLOAD, ref: str | None = "ZD-1") -> dict[str, Any]:
    return {
        "type": "run_start",
        "run_id": run_id,
        "workload": workload,
        "external_ref": ref,
        "started_at": ts(-10),
        "bundle_id": None,
        "bundle_version": None,
        "mode": "shadow",
        "holdout": False,
    }


def step(run_id: str, seq: int, cost: str = "0.001") -> dict[str, Any]:
    return {
        "type": "step",
        "run_id": run_id,
        "seq": seq,
        "ts": ts(-10 + seq),
        "kind": "tool",
        "model": None,
        "tool_name": "search",
        "args_hash": "hmac-sha256:" + "ab" * 32,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": cost,
        "error": False,
        "latency_ms": 12,
        "decision": {
            "action": "CONTINUE",
            "score": "0.25",
            "threshold": "0.9",
            "would_abort": False,
            "reason": "below_threshold",
        },
    }


def run_end(run_id: str, steps: int, total: str) -> dict[str, Any]:
    return {
        "type": "run_end",
        "run_id": run_id,
        "ended_at": ts(),
        "status": "completed",
        "total_cost_usd": total,
        "steps": steps,
        "would_abort_step": None,
        "cost_through_would_abort_usd": None,
        "aborted_step": None,
    }


def outcome(run_id: str, value: str, source: str = "sdk") -> dict[str, Any]:
    return {
        "type": "outcome",
        "run_id": run_id,
        "outcome": value,
        "source": source,
        "reported_at": ts(),
    }


def post(api: str, key: str, body: Any, path: str = "/v1/ingest", **kw: Any) -> httpx.Response:
    return httpx.post(
        f"{api}{path}",
        json=body,
        headers={"Authorization": f"Bearer {key}", **kw.pop("headers", {})},
        timeout=10,
        **kw,
    )


Admin = Any  # the db_admin fixture: (sql, params) -> rows


_admin: list[Admin] = []


@pytest.fixture(autouse=True)
def _bind_admin(db_admin: Admin) -> None:
    _admin[:] = [db_admin]


def admin(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Superuser SQL through the db_admin fixture (fixtures and assertions only)."""
    rows: list[dict[str, Any]] = _admin[0](sql, params)
    return rows


def admin_run(org_id: str, run_id: str) -> dict[str, Any]:
    rows = admin("select * from public.runs where org_id = %s and id = %s", (org_id, run_id))
    assert len(rows) == 1
    return rows[0]


# -- ingest ----------------------------------------------------------------------------------------


def test_ingest_is_idempotent_across_duplicate_batches(api: str, tenant: Any) -> None:
    rid = uuid7()
    body = batch(
        [
            run_start(rid),
            step(rid, 1),
            step(rid, 2),
            run_end(rid, 2, "0.002"),
            outcome(rid, "success"),
        ]
    )
    first = post(api, tenant.key, body)
    assert first.status_code == 200, first.text
    assert first.json() == {"accepted": 5, "duplicates": 0, "rejected": []}
    second = post(api, tenant.key, body)
    assert second.json() == {"accepted": 0, "duplicates": 5, "rejected": []}
    run = admin_run(tenant.org_id, rid)
    assert (run["status"], run["steps"], str(run["total_cost_usd"])) == (
        "completed",
        2,
        "0.002000000",
    )
    assert run["outcome"] == "success"
    steps = admin(
        "select seq, score, reason from public.step_events "
        "where org_id = %s and run_id = %s order by seq",
        (tenant.org_id, rid),
    )
    assert [(s["seq"], s["score"], s["reason"]) for s in steps] == [
        (1, 0.25, "below_threshold"),
        (2, 0.25, "below_threshold"),
    ]


def test_gzip_bodies_are_accepted(api: str, tenant: Any) -> None:
    rid = uuid7()
    raw = gzip.compress(json.dumps(batch([run_start(rid)])).encode())
    r = httpx.post(
        f"{api}/v1/ingest",
        content=raw,
        timeout=10,
        headers={
            "Authorization": f"Bearer {tenant.key}",
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert r.json()["accepted"] == 1


def test_placeholder_run_is_filled_by_a_late_run_start(api: str, tenant: Any) -> None:
    rid = uuid7()
    r = post(api, tenant.key, batch([step(rid, 1), run_end(rid, 1, "0.001")]))
    assert r.json()["accepted"] == 2
    run = admin_run(tenant.org_id, rid)
    assert (run["status"], run["workload_id"]) == ("partial", None)
    r = post(api, tenant.key, batch([run_start(rid)]))
    assert r.json()["accepted"] == 1
    run = admin_run(tenant.org_id, rid)
    assert run["status"] == "completed"
    assert run["workload_id"] is not None


def test_bad_events_are_rejected_individually(api: str, tenant: Any) -> None:
    rid = uuid7()
    bad = step(rid, 1)
    bad["cost_usd"] = 0.5  # money must be a decimal string
    r = post(api, tenant.key, batch([run_start(rid), bad, step(rid, 2)]))
    body = r.json()
    assert (body["accepted"], body["duplicates"]) == (2, 0)
    assert [x["index"] for x in body["rejected"]] == [1]


def test_invalid_envelope_is_a_problem(api: str, tenant: Any) -> None:
    r = post(api, tenant.key, {"schema_version": "1", "events": []})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    doc = r.json()
    assert doc["type"].endswith("/invalid-batch")
    assert doc["request_id"] == r.headers["x-request-id"]


def test_oversized_and_browser_requests_are_refused(api: str, tenant: Any) -> None:
    huge = batch([run_start(uuid7())])
    huge["events"][0]["external_ref"] = "x" * 1_100_000
    assert post(api, tenant.key, huge).status_code == 413
    r = post(
        api, tenant.key, batch([run_start(uuid7())]), headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403


def test_api_key_checks(api: str, tenant: Any) -> None:
    assert post(api, "sc_live_aaaaaaaa_" + "b" * 32, batch([run_start(uuid7())])).status_code == 401
    assert post(api, "not-a-key", batch([run_start(uuid7())])).status_code == 401
    r = httpx.post(f"{api}/v1/ingest", json=batch([run_start(uuid7())]), timeout=10)
    assert r.status_code == 401
    # A key without the ingest scope.
    key = httpx.post(
        f"{api}/v1/api-keys",
        json={"project_id": tenant.project_id, "scopes": ["bundle:read"]},
        headers=tenant.account.headers(tenant.org_id),
        timeout=10,
    ).json()
    assert post(api, key["key"], batch([run_start(uuid7())])).status_code == 403
    # Revocation takes effect on the next request.
    r = httpx.delete(
        f"{api}/v1/api-keys/{key['id']}", headers=tenant.account.headers(tenant.org_id), timeout=10
    )
    assert r.status_code == 200
    assert (
        httpx.get(
            f"{api}/v1/bundles/active",
            params={"workload": WORKLOAD},
            headers={"Authorization": f"Bearer {key['key']}"},
            timeout=10,
        ).status_code
        == 401
    )


# -- outcomes --------------------------------------------------------------------------------------


def test_late_outcome_by_external_ref_and_conflict(api: str, tenant: Any) -> None:
    rid, ref = uuid7(), f"ZD-{uuid.uuid4().hex[:6]}"
    post(api, tenant.key, batch([run_start(rid, ref=ref), run_end(rid, 0, "0")]))
    body = {
        "schema_version": "1",
        "workload": WORKLOAD,
        "external_ref": ref,
        "outcome": "failure",
        "source": "zendesk",
    }
    r = post(api, tenant.key, body, path="/v1/outcomes")
    assert r.json() == {"run_id": rid, "status": "recorded"}
    assert post(api, tenant.key, body, path="/v1/outcomes").json()["status"] == "duplicate"
    r = post(api, tenant.key, {**body, "outcome": "success"}, path="/v1/outcomes")
    assert r.json()["status"] == "conflict"
    run = admin_run(tenant.org_id, rid)
    assert (run["outcome"], run["outcome_conflict"]) == ("failure", True)  # the first outcome wins
    audit = admin(
        "select action, before, after from public.audit_log where org_id = %s and target = %s",
        (tenant.org_id, f"run:{rid}"),
    )
    assert [(a["action"], a["before"]["outcome"], a["after"]["outcome"]) for a in audit] == [
        ("outcome_conflict", "failure", "success")
    ]


def test_outcome_before_any_event_creates_a_placeholder(api: str, tenant: Any) -> None:
    rid = uuid7()
    body = {"schema_version": "1", "run_id": rid, "outcome": "success", "source": "ci"}
    assert post(api, tenant.key, body, path="/v1/outcomes").json()["status"] == "recorded"
    assert admin_run(tenant.org_id, rid)["status"] == "partial"
    post(api, tenant.key, batch([run_start(rid)]))
    run = admin_run(tenant.org_id, rid)
    assert (run["status"], run["outcome"]) == ("running", "success")


def test_unknown_external_ref_is_404(api: str, tenant: Any) -> None:
    body = {
        "schema_version": "1",
        "workload": WORKLOAD,
        "external_ref": "nope",
        "outcome": "success",
        "source": "x",
    }
    r = post(api, tenant.key, body, path="/v1/outcomes")
    assert r.status_code == 404
    assert r.json()["status"] == 404


# -- tenant isolation through the API --------------------------------------------------------------


def test_tenants_cannot_touch_each_others_runs(api: str, tenant: Any, other_tenant: Any) -> None:
    rid = uuid7()
    post(api, tenant.key, batch([run_start(rid), step(rid, 1)]))
    # The same run_id from another org is a different run: it neither sees nor changes A's.
    r = post(api, other_tenant.key, batch([step(rid, 1), outcome(rid, "failure")]))
    assert r.json()["accepted"] == 2
    a, b = admin_run(tenant.org_id, rid), admin_run(other_tenant.org_id, rid)
    assert (a["status"], a["outcome"]) == ("running", None)
    assert (b["status"], b["outcome"]) == ("partial", "failure")
    # B's user cannot manage A's org.
    r = httpx.post(
        f"{api}/v1/projects",
        json={"name": "evil"},
        headers=other_tenant.account.headers(tenant.org_id),
        timeout=10,
    )
    assert r.status_code == 404


def test_runs_cannot_cross_projects(api: str, tenant: Any) -> None:
    headers = tenant.account.headers(tenant.org_id)
    project = httpx.post(
        f"{api}/v1/projects", json={"name": "second"}, headers=headers, timeout=10
    ).json()
    key = httpx.post(
        f"{api}/v1/api-keys",
        json={"project_id": project["id"], "scopes": ["ingest"]},
        headers=headers,
        timeout=10,
    ).json()["key"]
    rid = uuid7()
    post(api, tenant.key, batch([run_start(rid)]))
    r = post(api, key, batch([step(rid, 1)]))
    assert r.json()["rejected"] == [{"index": 0, "reason": "run belongs to another project"}]


# -- management ------------------------------------------------------------------------------------


def test_roles_are_enforced(api: str, new_user: Any, tenant: Any) -> None:
    viewer = new_user()
    admin(
        "insert into public.org_members (org_id, user_id, role) values (%s, %s, 'viewer')",
        (tenant.org_id, viewer.user_id),
    )
    r = httpx.post(
        f"{api}/v1/api-keys",
        json={"project_id": tenant.project_id, "scopes": ["ingest"]},
        headers=viewer.headers(tenant.org_id),
        timeout=10,
    )
    assert r.status_code == 403
    assert httpx.post(f"{api}/v1/orgs", json={"name": "x"}, timeout=10).status_code == 401
    assert (
        httpx.post(
            f"{api}/v1/orgs",
            json={"name": "x"},
            headers={"Authorization": "Bearer abc.def.ghi"},
            timeout=10,
        ).status_code
        == 401
    )


def test_workload_settings_mode_change_enqueues_resign(api: str, tenant: Any) -> None:
    headers = tenant.account.headers(tenant.org_id)
    w = httpx.post(
        f"{api}/v1/workloads",
        json={"project_id": tenant.project_id, "name": "ops.triage", "target_recall": "0.9"},
        headers=headers,
        timeout=10,
    )
    assert w.status_code == 201, w.text
    wid = w.json()["id"]
    assert w.json()["target_recall"] == "0.9000"
    r = httpx.patch(
        f"{api}/v1/workloads/{wid}", json={"mode": "enforce"}, headers=headers, timeout=10
    )
    assert r.status_code == 200
    job = admin(
        "select type, payload, status from public.jobs where id = %s", (r.json()["resign_job_id"],)
    )[0]
    assert (job["type"], job["payload"]["mode"], job["status"]) == (
        "resign_bundle",
        "enforce",
        "queued",
    )
    c = httpx.post(f"{api}/v1/workloads/{wid}/calibrate", headers=headers, timeout=10)
    assert c.status_code == 202
    assert (
        admin("select type from public.jobs where id = %s", (c.json()["job_id"],))[0]["type"]
        == "calibrate"
    )
    bad = httpx.patch(
        f"{api}/v1/workloads/{wid}", json={"delta": "1.5"}, headers=headers, timeout=10
    )
    assert bad.status_code == 422


# -- bundles ---------------------------------------------------------------------------------------


def seed_bundle(
    tenant: Any, workload_id: str, version: int, status: str = "validated", **kw: Any
) -> str:
    """Insert a signed bundle as the worker will in P4 (test key)."""
    doc = make_bundle(WORKLOAD, version=version, issued_at_us=time.time_ns() // 1000, **kw)
    signed = sign(doc)
    rows = admin(
        "insert into public.policy_bundles "
        "(id, org_id, workload_id, version, status, payload, signature, key_id) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            doc["bundle_id"],
            tenant.org_id,
            workload_id,
            version,
            status,
            json.dumps(doc),
            signed["signature"],
            signed["key_id"],
        ),
    )
    return str(rows[0]["id"])


def workload_id(api: str, tenant: Any) -> str:
    post(api, tenant.key, batch([run_start(uuid7())]))  # auto-creates the workload
    rows = admin(
        "select id from public.workloads where org_id = %s and name = %s", (tenant.org_id, WORKLOAD)
    )
    return str(rows[0]["id"])


def test_bundle_serving_activation_etag_and_override(
    api: str, tenant: Any, other_tenant: Any
) -> None:
    wid = workload_id(api, tenant)
    headers = {"Authorization": f"Bearer {tenant.key}"}
    url = f"{api}/v1/bundles/active"
    assert (
        httpx.get(url, params={"workload": WORKLOAD}, headers=headers, timeout=10).status_code
        == 404
    )

    bid = seed_bundle(tenant, wid, 1)
    user = tenant.account.headers(tenant.org_id)
    rejected = seed_bundle(tenant, wid, 2, status="rejected")
    assert (
        httpx.post(f"{api}/v1/bundles/{rejected}/activate", headers=user, timeout=10).status_code
        == 409
    )
    r = httpx.post(f"{api}/v1/bundles/{bid}/activate", headers=user, timeout=10)
    assert r.json()["status"] == "active"

    r = httpx.get(url, params={"workload": WORKLOAD}, headers=headers, timeout=10)
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert r.json()["bundle"]["bundle_id"] == bid
    assert (
        httpx.get(
            url,
            params={"workload": WORKLOAD},
            headers={**headers, "If-None-Match": etag},
            timeout=10,
        ).status_code
        == 304
    )

    # Override: immediate, unsigned, changes the ETag; a member may set it.
    r = httpx.post(
        f"{api}/v1/workloads/{wid}/override", json={"override": "shadow"}, headers=user, timeout=10
    )
    assert r.json()["override"] == "shadow"
    r = httpx.get(
        url, params={"workload": WORKLOAD}, headers={**headers, "If-None-Match": etag}, timeout=10
    )
    assert r.status_code == 200
    assert r.json()["override"] == "shadow"
    assert r.headers["etag"] != etag

    # Another tenant's key never sees this bundle.
    other = {"Authorization": f"Bearer {other_tenant.key}"}
    assert (
        httpx.get(url, params={"workload": WORKLOAD}, headers=other, timeout=10).status_code == 404
    )


def test_sdk_end_to_end_against_the_real_api(api: str, tenant: Any) -> None:
    """The SDK verifies a bundle served by the API, aborts, and its events land in Postgres."""
    wid = workload_id(api, tenant)
    bid = seed_bundle(tenant, wid, 1, threshold="0.9", min_steps=2, weights={"error_streak": "2.0"})
    httpx.post(
        f"{api}/v1/bundles/{bid}/activate",
        headers=tenant.account.headers(tenant.org_id),
        timeout=10,
    )
    admin("update public.workloads set override = null where id = %s", (wid,))
    sl = Sunkcost(
        api_key=tenant.key,
        endpoint=api,
        trusted_keys=TEST_TRUSTED_KEYS,
        args_hash_salt="e2e",
        workloads=[WORKLOAD],
        flush_interval_s=0.1,
    )
    sl.refresh_bundles()
    run = sl.start_run(WORKLOAD, external_ref="E2E-1")
    reasons = []
    for _ in range(10):
        d = run.check()
        reasons.append(d.reason)
        if d.abort:
            break
        run.record_step(
            "tool", tool_name="run_tests", tool_args={"t": 1}, error=True, cost_usd="0.01"
        )
    run.end("aborted")
    run.outcome("failure")
    assert sl.flush(10)
    sl.close()
    assert reasons == ["below_min_steps", "below_min_steps", "above_threshold"]
    db_run = admin_run(tenant.org_id, run.run_id)
    assert (db_run["status"], db_run["mode"], str(db_run["bundle_id"])) == (
        "aborted",
        "enforce",
        bid,
    )
    assert (db_run["aborted_step"], db_run["would_abort_step"], str(db_run["total_cost_usd"])) == (
        2,
        2,
        "0.020000000",
    )
    assert db_run["outcome"] == "failure"


def test_signing_keys_and_health(api: str) -> None:
    admin(
        "insert into public.signing_keys (key_id, public_key_b64) values ('rfc8032-test-1', %s) "
        "on conflict do nothing",
        (TEST_PUBLIC_KEY_B64,),
    )
    r = httpx.get(f"{api}/v1/signing-keys", timeout=10)
    assert "rfc8032-test-1" in [k["key_id"] for k in r.json()["keys"]]
    assert httpx.get(f"{api}/healthz", timeout=10).json() == {"status": "ok"}
    assert httpx.get(f"{api}/readyz", timeout=10).status_code == 200
    assert "sunkcost_api_requests_total" in httpx.get(f"{api}/metrics", timeout=10).text
    r = httpx.get(f"{api}/v1/nope", timeout=10)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
