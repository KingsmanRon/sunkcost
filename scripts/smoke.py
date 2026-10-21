"""Smoke test against a deployed stack: G7 for staging (§13), and the production smoke (§11.4).

    make smoke-staging                         # full: everything below
    uv run python scripts/smoke.py --quick     # production: health, ingest, outcome, bundle fetch

Full run, as a customer would see it, in a dedicated organisation the smoke user owns:

1. health (/healthz, /readyz) and the published signing key (/v1/signing-keys)
2. ingest of 1,000 labelled synthetic runs; resending a batch is idempotent
3. a late outcome through POST /v1/outcomes, read back through PostgREST under RLS
4. no bundle before calibration (404); calibration on the synthetic data → a validated bundle
5. activation; the Python SDK fetches it and verifies its signature against the pinned key
6. drift: successful runs the bundle would have aborted → the worker downgrades the workload, and
   the SDK observes the override (override_shadow) without any restart

Configuration comes from the environment (docs/DEPLOYMENT.md lists every variable). The smoke
never touches the database directly and never uses the service_role key.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx

from sunkcost import Sunkcost
from sunkcost._money import money_str
from sunkcost.core import format_timestamp_us
from sunkcost_worker.synthetic import WorkloadProcess, generate_run

DAY_US = 86_400_000_000
ORG_NAME = "sunkcost-smoke"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"smoke: {name} is not set (see docs/DEPLOYMENT.md)")
    return value


class Smoke:
    def __init__(self) -> None:
        self.api = env("SMOKE_API_URL").rstrip("/")
        self.supabase = env("SMOKE_SUPABASE_URL").rstrip("/")
        self.anon = env("SMOKE_SUPABASE_ANON_KEY")
        self.http = httpx.Client(timeout=30)
        self.results: list[tuple[str, bool, str, float]] = []
        self.token = ""
        self.org = ""
        self.key = ""
        self.key_id = ""

    # -- plumbing ---------------------------------------------------------------------------

    def step(self, name: str, fn: Callable[[], str]) -> None:
        t0 = time.monotonic()
        try:
            detail = fn()
            ok = True
        except Exception as exc:  # every failure is reported, then the run stops
            detail, ok = f"{type(exc).__name__}: {exc}", False
        self.results.append((name, ok, detail, time.monotonic() - t0))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.monotonic() - t0:.1f} s)  {detail}")
        if not ok:
            raise SystemExit(self.report())

    def user(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}", "x-sunkcost-org": self.org}

    def sdk_key(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def rest(self, path: str) -> list[dict[str, Any]]:
        r = self.http.get(
            f"{self.supabase}/rest/v1/{path}",
            headers={"apikey": self.anon, "authorization": f"Bearer {self.token}"},
        )
        r.raise_for_status()
        rows: list[dict[str, Any]] = r.json()
        return rows

    def ingest(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        out = {"accepted": 0, "duplicates": 0, "rejected": 0}
        for i in range(0, len(events), 1000):
            body = {
                "schema_version": "1",
                "sent_at": format_timestamp_us(time.time_ns() // 1000),
                "sdk": {"name": "sunkcost-smoke", "version": "1"},
                "dropped_events": 0,
                "events": events[i : i + 1000],
            }
            r = self.http.post(f"{self.api}/v1/ingest", json=body, headers=self.sdk_key())
            r.raise_for_status()
            doc = r.json()
            out["accepted"] += doc["accepted"]
            out["duplicates"] += doc["duplicates"]
            out["rejected"] += len(doc["rejected"])
        return out

    def report(self) -> int:
        passed = all(ok for _, ok, _, _ in self.results)
        print(
            f"\n{'PASS' if passed else 'FAIL'}: {sum(ok for _, ok, _, _ in self.results)}"
            f"/{len(self.results)} smoke steps passed"
        )
        return 0 if passed else 1

    # -- steps ------------------------------------------------------------------------------

    def health(self) -> str:
        for path in ("/healthz", "/readyz"):
            r = self.http.get(f"{self.api}{path}")
            if r.status_code != 200:
                raise AssertionError(f"{path} returned {r.status_code}")
        key_id, public = env("SMOKE_SIGNING_KEY_ID"), env("SMOKE_SIGNING_PUBLIC_KEY")
        keys = self.http.get(f"{self.api}/v1/signing-keys").json()["keys"]
        if not any(k["key_id"] == key_id and k["public_key_b64"] == public for k in keys):
            raise AssertionError(f"signing key {key_id} is not published with the pinned value")
        return f"healthy; signing key {key_id} published"

    def sign_in(self) -> str:
        r = self.http.post(
            f"{self.supabase}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": self.anon},
            json={"email": env("SMOKE_USER_EMAIL"), "password": env("SMOKE_USER_PASSWORD")},
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return "signed in with Supabase Auth"

    def tenant(self) -> str:
        mine = [o for o in self.rest("orgs?select=id,name") if o["name"] == ORG_NAME]
        if mine:
            self.org = mine[0]["id"]
        else:
            r = self.http.post(
                f"{self.api}/v1/orgs",
                json={"name": ORG_NAME},
                headers={"authorization": f"Bearer {self.token}"},
            )
            r.raise_for_status()
            self.org = r.json()["id"]
        projects = self.rest(f"projects?select=id&org_id=eq.{self.org}&name=eq.smoke")
        if projects:
            project = projects[0]["id"]
        else:
            r = self.http.post(
                f"{self.api}/v1/projects", json={"name": "smoke"}, headers=self.user()
            )
            r.raise_for_status()
            project = r.json()["id"]
        r = self.http.post(
            f"{self.api}/v1/api-keys",
            json={
                "project_id": project,
                "name": "smoke run",
                "scopes": ["ingest", "outcome:write", "bundle:read"],
            },
            headers=self.user(),
        )
        r.raise_for_status()
        self.key, self.key_id = r.json()["key"], r.json()["id"]
        return f"org {self.org}, fresh API key {r.json()['prefix']}"

    def revoke(self) -> str:
        r = self.http.delete(f"{self.api}/v1/api-keys/{self.key_id}", headers=self.user())
        r.raise_for_status()
        return "smoke API key revoked"

    def workload_id(self, name: str) -> str:
        rows = self.rest(f"workloads?select=id&org_id=eq.{self.org}&name=eq.{name}")
        if not rows:
            raise AssertionError(f"workload {name} not created by ingest")
        return str(rows[0]["id"])

    def wait_job(self, job_id: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rows = self.rest(f"jobs?select=status,result,error&id=eq.{job_id}")
            if rows and rows[0]["status"] == "done":
                result: dict[str, Any] = rows[0]["result"]
                return result
            if rows and rows[0]["status"] == "dead":
                raise AssertionError(f"job {job_id} died: {rows[0]['error']}")
            time.sleep(3)
        raise AssertionError(f"job {job_id} did not finish within {timeout_s:.0f} s")

    def client(self, workload: str) -> Sunkcost:
        return Sunkcost(
            api_key=self.key,
            endpoint=self.api,
            trusted_keys={env("SMOKE_SIGNING_KEY_ID"): env("SMOKE_SIGNING_PUBLIC_KEY")},
            args_hash_salt="smoke",
            workloads=[workload],
        )


def run_events(run: Any, workload: str, **end: Any) -> list[dict[str, Any]]:
    """The events an SDK would send for a synthetic run (the outcome only if it has one)."""
    start = {
        "type": "run_start",
        "run_id": run.run_id,
        "workload": workload,
        "external_ref": None,
        "started_at": format_timestamp_us(run.started_at_us),
        "bundle_id": end.pop("bundle_id", None),
        "bundle_version": end.pop("bundle_version", None),
        "mode": "shadow",
        "holdout": False,
    }
    steps = [
        {
            "type": "step",
            "run_id": run.run_id,
            "seq": i,
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
        for i, (s, cost) in enumerate(zip(run.steps, run.costs, strict=True), start=1)
    ]
    finish = {
        "type": "run_end",
        "run_id": run.run_id,
        "ended_at": format_timestamp_us(run.steps[-1].ts_us + 1000),
        "status": "completed",
        "total_cost_usd": money_str(run.total_cost),
        "steps": run.n_steps,
        "would_abort_step": None,
        "cost_through_would_abort_usd": None,
        "aborted_step": None,
        **end,
    }
    events = [start, *steps, finish]
    if run.outcome is not None:
        events.append(
            {
                "type": "outcome",
                "run_id": run.run_id,
                "outcome": run.outcome,
                "source": "smoke",
                "reported_at": format_timestamp_us(time.time_ns() // 1000),
            }
        )
    return events


def full(smoke: Smoke) -> None:
    workload = f"smoke.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    rng = random.Random(1)  # noqa: S311 - seeded test data
    now_us = time.time_ns() // 1000
    history = [
        generate_run(rng, WorkloadProcess(), now_us - 8 * DAY_US + i * 600_000_000)
        for i in range(1000)
    ]
    events = [e for r in history for e in run_events(r, workload)]
    state: dict[str, Any] = {}

    def ingest() -> str:
        first = smoke.ingest(events[:1000])
        again = smoke.ingest(events[:1000])  # the SDK resends after a timeout: must deduplicate
        rest = smoke.ingest(events[1000:])
        if first["rejected"] or rest["rejected"] or again["accepted"]:
            raise AssertionError(f"ingest {first} / resend {again} / rest {rest}")
        state["wid"] = smoke.workload_id(workload)
        total = first["accepted"] + rest["accepted"]
        return f"{total} events; the resend gave {again['duplicates']} duplicates"

    def outcome() -> str:
        late = replace(generate_run(rng, WorkloadProcess(), now_us - 3_600_000_000), outcome=None)
        smoke.ingest(run_events(late, workload))
        r = smoke.http.post(
            f"{smoke.api}/v1/outcomes",
            headers=smoke.sdk_key(),
            json={
                "schema_version": "1",
                "run_id": late.run_id,
                "outcome": "success",
                "source": "smoke",
            },
        )
        r.raise_for_status()
        rows = smoke.rest(f"runs?select=outcome&org_id=eq.{smoke.org}&id=eq.{late.run_id}")
        if not rows or rows[0]["outcome"] != "success":
            raise AssertionError(f"outcome not recorded: {rows}")
        return "late outcome recorded and readable under RLS"

    def calibrate() -> str:
        r = smoke.http.get(
            f"{smoke.api}/v1/bundles/active", params={"workload": workload}, headers=smoke.sdk_key()
        )
        if r.status_code != 404:
            raise AssertionError(f"expected 404 before calibration, got {r.status_code}")
        r = smoke.http.post(
            f"{smoke.api}/v1/workloads/{state['wid']}/calibrate", headers=smoke.user()
        )
        r.raise_for_status()
        result = smoke.wait_job(
            r.json()["job_id"], float(os.environ.get("SMOKE_JOB_TIMEOUT_S", "600"))
        )
        if result.get("status") != "validated":
            raise AssertionError(f"calibration result {result}")
        state["bundle"] = result["bundle_id"]
        return f"validated bundle from {result['runs_used']} runs"

    def activate() -> str:
        r = smoke.http.post(
            f"{smoke.api}/v1/bundles/{state['bundle']}/activate", headers=smoke.user()
        )
        r.raise_for_status()
        sl = smoke.client(workload)
        sl.refresh_bundles()
        run = sl.start_run(workload)
        state["sdk"] = sl
        if run.bundle is None or run.bundle.bundle_id != state["bundle"]:
            raise AssertionError(f"SDK did not accept the bundle (rejected {sl.bundles.rejected})")
        run.end()
        return f"SDK verified bundle v{run.bundle.version} signed by {run.bundle.key_id}"

    def drift() -> str:
        # 40 successful runs of the active bundle, 30 of which it would have aborted (α = 0.05).
        version = smoke.rest(f"policy_bundles?select=version&id=eq.{state['bundle']}")[0]["version"]
        runs = []
        for i in range(40):
            started = time.time_ns() // 1000 - 600_000_000 + i
            r = replace(generate_run(rng, WorkloadProcess(), started), outcome="success")
            extra = {"would_abort_step": 1, "cost_through_would_abort_usd": "0.1"} if i < 30 else {}
            runs += run_events(
                r, workload, bundle_id=state["bundle"], bundle_version=version, **extra
            )
        smoke.ingest(runs)
        sl: Sunkcost = state["sdk"]
        timeout = float(os.environ.get("SMOKE_DRIFT_TIMEOUT_S", "900"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sl.refresh_bundles()
            if sl.bundles.override(workload) == "shadow":
                run = sl.start_run(workload)
                for _ in range(run.bundle.min_steps if run.bundle else 0):
                    run.record_step("tool", tool_name="smoke")
                d = run.check()
                run.end()
                if d.reason not in ("override_shadow", "below_threshold"):
                    raise AssertionError(f"SDK decision under override: {d}")
                audit = smoke.rest(
                    f"audit_log?select=actor,action&org_id=eq.{smoke.org}"
                    f"&target=eq.workload:{state['wid']}&action=eq.workload.override"
                )
                if not any(a["actor"] == "system" for a in audit):
                    raise AssertionError("no system audit entry for the downgrade")
                return f"downgraded to shadow by the worker; SDK saw it ({d.reason})"
            time.sleep(10)
        raise AssertionError(
            f"no drift downgrade within {timeout:.0f} s (worker DRIFT_INTERVAL_S?)"
        )

    smoke.step("ingest 1,000 runs, idempotent resend", ingest)
    smoke.step("late outcome via /v1/outcomes", outcome)
    smoke.step("calibration → validated bundle", calibrate)
    smoke.step("activation; SDK fetches and verifies", activate)
    smoke.step("drift downgrade observed by the SDK", drift)
    state["sdk"].close()


def quick(smoke: Smoke) -> None:
    workload = "smoke.prod"
    rng = random.Random()  # noqa: S311 - test data
    run = generate_run(rng, WorkloadProcess(), time.time_ns() // 1000 - 60_000_000)

    def ingest() -> str:
        got = smoke.ingest(run_events(run, workload))
        if got["rejected"] or not got["accepted"]:
            raise AssertionError(str(got))
        return f"{got['accepted']} events accepted"

    def bundle() -> str:
        sl = smoke.client(workload)
        sl.refresh_bundles()
        d = sl.start_run(workload).check()
        sl.close()
        if d.reason not in ("no_bundle", "below_min_steps", "below_threshold", "shadow_mode"):
            raise AssertionError(f"unexpected decision {d}")
        return f"SDK decision {d.reason} (bundles rejected: {sl.bundles.rejected})"

    smoke.step("ingest a synthetic run with its outcome", ingest)
    smoke.step("bundle fetch through the SDK", bundle)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--quick", action="store_true", help="production smoke: no calibration or drift"
    )
    args = parser.parse_args()
    smoke = Smoke()
    smoke.step("health and published signing key", smoke.health)
    smoke.step("sign in", smoke.sign_in)
    smoke.step("organisation, project, API key", smoke.tenant)
    (quick if args.quick else full)(smoke)
    smoke.step("revoke the smoke key", smoke.revoke)
    return smoke.report()


if __name__ == "__main__":
    sys.exit(main())
