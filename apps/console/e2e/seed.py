"""Test data for the console e2e (G6b), run by the Playwright spec with ``uv run``.

history   ingest 1,000 labelled shadow runs spread over the 8 days before today, through the API
report    enqueue the daily and trailing-week reports for those days and wait for the worker
sdk-runs  run a few agents through the real SDK, so the run explorer has scores and thresholds

Only ``report`` touches Postgres directly (as the local admin, to enqueue jobs the scheduler would
otherwise enqueue overnight); everything else goes through the API like a customer would.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

from sunkcost import Sunkcost
from sunkcost._money import money_str
from sunkcost.core import format_timestamp_us
from sunkcost.testing import TEST_TRUSTED_KEYS
from sunkcost_worker.synthetic import WorkloadProcess, generate_run

ADMIN_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DAY_US = 86_400_000_000


def run_events(run: Any, workload: str) -> list[dict[str, Any]]:
    rid = run.run_id
    events: list[dict[str, Any]] = [
        {
            "type": "run_start",
            "run_id": rid,
            "workload": workload,
            "external_ref": None,
            "started_at": format_timestamp_us(run.started_at_us),
            "bundle_id": None,
            "bundle_version": None,
            "mode": "shadow",
            "holdout": False,
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
    events.append(
        {
            "type": "run_end",
            "run_id": rid,
            "ended_at": format_timestamp_us(run.steps[-1].ts_us + 1000),
            "status": "completed",
            "total_cost_usd": money_str(run.total_cost),
            "steps": run.n_steps,
            "would_abort_step": None,
            "cost_through_would_abort_usd": None,
            "aborted_step": None,
        }
    )
    events.append(
        {
            "type": "outcome",
            "run_id": rid,
            "outcome": run.outcome,
            "source": "e2e",
            "reported_at": format_timestamp_us(time.time_ns() // 1000),
        }
    )
    return events


def today_us() -> int:
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return int(start.timestamp()) * 1_000_000


def history(api: str, key: str, workload: str) -> None:
    rng = random.Random(1)  # noqa: S311 - seeded test data
    process = WorkloadProcess()
    first = today_us() - 8 * DAY_US + 3_600_000_000  # 01:00 eight days ago
    spacing = (8 * DAY_US - 2 * 3_600_000_000) // 1000
    runs = [generate_run(rng, process, first + i * spacing) for i in range(1000)]
    events = [e for r in runs for e in run_events(r, workload)]
    for i in range(0, len(events), 1000):
        body = {
            "schema_version": "1",
            "sent_at": format_timestamp_us(time.time_ns() // 1000),
            "sdk": {"name": "sunkcost-e2e", "version": "0"},
            "dropped_events": 0,
            "events": events[i : i + 1000],
        }
        r = httpx.post(
            f"{api}/v1/ingest", json=body, headers={"authorization": f"Bearer {key}"}, timeout=30
        )
        r.raise_for_status()
        if r.json()["rejected"]:
            sys.exit(f"ingest rejected events: {r.json()['rejected'][:3]}")


def report(org: str, workload: str) -> None:
    today = datetime.fromtimestamp(today_us() / 1e6, UTC)
    periods = [(today - timedelta(days=7), today)]
    periods += [(today - timedelta(days=d + 1), today - timedelta(days=d)) for d in range(8)]
    with psycopg.connect(ADMIN_DB_URL, autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            "select id from public.workloads where org_id = %s and name = %s", (org, workload)
        ).fetchone()
        if row is None:
            sys.exit(f"no workload {workload!r} in org {org}")
        ids = [
            conn.execute(
                "insert into public.jobs (org_id, type, payload) values (%s, 'build_report', %s) "
                "returning id",
                (
                    org,
                    json.dumps(
                        {
                            "workload_id": str(row["id"]),
                            "period_start": start.isoformat(),
                            "period_end": end.isoformat(),
                        }
                    ),
                ),
            ).fetchone()["id"]  # type: ignore[index]
            for start, end in periods
        ]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            states = conn.execute(
                "select status, error from public.jobs where id = any(%s)", (ids,)
            ).fetchall()
            if all(s["status"] == "done" for s in states):
                return
            if any(s["status"] == "dead" for s in states):
                sys.exit(f"report job died: {states}")
            time.sleep(0.5)
    sys.exit("report jobs did not finish within 60 s")


def sdk_runs(api: str, key: str, workload: str) -> None:
    client = Sunkcost(
        api_key=key,
        endpoint=api,
        trusted_keys=TEST_TRUSTED_KEYS,
        args_hash_salt="e2e",
        workloads=[workload],
    )
    client.refresh_bundles()
    for i in range(4):
        run = client.start_run(workload)
        for step in range(30):
            if run.check().abort:
                break
            # The same failing tool call over and over: the pattern the model learnt to flag.
            run.record_step("tool", tool_name="run_tests", tool_args={"k": i}, error=step % 4 != 0)
        run.end("completed")
        run.outcome("failure")
    if not client.close(10):
        sys.exit("the SDK could not deliver its events")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["history", "report", "sdk-runs"])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--key", default="")
    parser.add_argument("--org", default="")
    parser.add_argument("--workload", required=True)
    args = parser.parse_args()
    if args.command == "history":
        history(args.api, args.key, args.workload)
    elif args.command == "report":
        report(args.org, args.workload)
    else:
        sdk_runs(args.api, args.key, args.workload)


if __name__ == "__main__":
    main()
