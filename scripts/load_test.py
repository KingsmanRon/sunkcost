"""Load test (§13 "Load"): sustained ingest at a target rate, recorded in docs/PERF.md either way.

    make load-test                                   # staging: 1,000 events/s for 10 minutes
    LOAD_DURATION_S=60 LOAD_TARGET=local uv run python scripts/load_test.py   # a local dry run

Signs in as the smoke user (the same SMOKE_* variables as scripts/smoke.py), creates a fresh API
key in the smoke organisation, then runs LOAD_CONCURRENCY senders posting gzip-compressed batches
of 500 events (whole runs: run_start, 10 steps, run_end, outcome) as the SDK would, paced to
LOAD_RATE events/s overall. It reports the sustained accepted rate, batch latency and every
non-2xx status, writes the "load" section of docs/PERF.md, and revokes the key.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import platform
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke import Smoke

from sunkcost._ids import uuid7
from sunkcost.core import format_timestamp_us

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- load:start -->", "<!-- load:end -->"
BATCH = 500
STEPS_PER_RUN = 10


def run_events(workload: str, now_us: int) -> list[dict[str, Any]]:
    rid = uuid7(now_us // 1000)
    ts = format_timestamp_us(now_us)
    events: list[dict[str, Any]] = [
        {
            "type": "run_start",
            "run_id": rid,
            "workload": workload,
            "external_ref": None,
            "started_at": ts,
            "bundle_id": None,
            "bundle_version": None,
            "mode": "shadow",
            "holdout": False,
        },
    ]
    for seq in range(1, STEPS_PER_RUN + 1):
        events.append(
            {
                "type": "step",
                "run_id": rid,
                "seq": seq,
                "ts": ts,
                "kind": "model",
                "model": "claude-sonnet-4-5",
                "tool_name": None,
                "args_hash": None,
                "input_tokens": 900 + seq,
                "output_tokens": 120,
                "cost_usd": "0.0045",
                "error": False,
                "latency_ms": 800,
                "decision": None,
            }
        )
    events.append(
        {
            "type": "run_end",
            "run_id": rid,
            "ended_at": ts,
            "status": "completed",
            "total_cost_usd": "0.045",
            "steps": STEPS_PER_RUN,
            "would_abort_step": None,
            "cost_through_would_abort_usd": None,
            "aborted_step": None,
        }
    )
    events.append(
        {
            "type": "outcome",
            "run_id": rid,
            "outcome": "success",
            "source": "load",
            "reported_at": ts,
        }
    )
    return events


def batch_body(workload: str) -> tuple[bytes, int]:
    now_us = time.time_ns() // 1000
    events: list[dict[str, Any]] = []
    while len(events) + STEPS_PER_RUN + 3 <= BATCH:
        events += run_events(workload, now_us)
    doc = {
        "schema_version": "1",
        "sent_at": format_timestamp_us(now_us),
        "sdk": {"name": "sunkcost-load", "version": "1"},
        "dropped_events": 0,
        "events": events,
    }
    return gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 5), len(events)


async def run(
    api: str, key: str, rate: float, duration_s: float, concurrency: int
) -> dict[str, Any]:
    workload = f"load.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    statuses: Counter[int] = Counter()
    latencies: list[float] = []
    accepted = 0
    sent = 0
    started = time.monotonic()
    deadline = started + duration_s
    per_batch = BATCH - BATCH % (STEPS_PER_RUN + 3)
    interval = per_batch / rate  # seconds between batches, overall
    next_slot = started
    lock = asyncio.Lock()
    headers = {
        "authorization": f"Bearer {key}",
        "content-type": "application/json",
        "content-encoding": "gzip",
    }

    async def sender(client: httpx.AsyncClient) -> None:
        nonlocal accepted, sent, next_slot
        while True:
            async with lock:
                slot = next_slot
                next_slot += interval
            if slot >= deadline:
                return
            await asyncio.sleep(max(0.0, slot - time.monotonic()))
            body, n = batch_body(workload)
            t0 = time.monotonic()
            try:
                r = await client.post(f"{api}/v1/ingest", content=body, headers=headers)
                statuses[r.status_code] += 1
                if r.status_code < 300:
                    accepted += int(r.json()["accepted"])
            except httpx.HTTPError as exc:
                statuses[-1] += 1
                print(f"request error: {type(exc).__name__}", file=sys.stderr)
            latencies.append(time.monotonic() - t0)
            sent += n

    async with httpx.AsyncClient(timeout=60) as client:
        await asyncio.gather(*(sender(client) for _ in range(concurrency)))
    elapsed = time.monotonic() - started
    latencies.sort()

    def pct(q: float) -> float:
        return (
            latencies[min(len(latencies) - 1, round(q * (len(latencies) - 1)))]
            if latencies
            else 0.0
        )

    return {
        "workload": workload,
        "elapsed": elapsed,
        "sent": sent,
        "accepted": accepted,
        "rate": accepted / elapsed if elapsed else 0.0,
        "p50": pct(0.5),
        "p99": pct(0.99),
        "max": latencies[-1] if latencies else 0.0,
        "statuses": dict(statuses),
        "batches": len(latencies),
    }


def render(r: dict[str, Any], target: str, rate: float, duration: float, concurrency: int) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d")
    met = r["rate"] >= rate * 0.98 and set(r["statuses"]) <= {200, 202}
    statuses = ", ".join(f"{k}: {v}" for k, v in sorted(r["statuses"].items())) or "none"
    latency = " / ".join(f"{r[q] * 1000:.0f}" for q in ("p50", "p99", "max"))
    return f"""{START}
## Ingest load test

Measured on {when} against **{target}** with `scripts/load_test.py`, from {platform.system()}
{platform.release()}: {concurrency} concurrent senders, gzip-compressed batches of
{BATCH - BATCH % (STEPS_PER_RUN + 3)} events, paced to {rate:,.0f} events/s for {duration:,.0f} s.

| Metric | Value |
|---|---|
| Events sent | {r["sent"]:,} in {r["batches"]:,} batches over {r["elapsed"]:.0f} s |
| Events accepted | {r["accepted"]:,} |
| Sustained accepted rate | **{r["rate"]:,.0f} events/s** (target {rate:,.0f}) |
| Batch latency p50 / p99 / max | {latency} ms |
| HTTP statuses | {statuses} |

**Target {"met" if met else "not met"}.**
{END}"""


def write(section: str) -> None:
    out = ROOT / "docs" / "PERF.md"
    text = out.read_text("utf-8")
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.DOTALL)
    text = (
        pattern.sub(lambda _: section, text)
        if pattern.search(text)
        else text.rstrip() + "\n\n" + section
    )
    out.write_text(text.rstrip() + "\n", "utf-8")


def main() -> int:
    rate = float(os.environ.get("LOAD_RATE", "1000"))
    duration = float(os.environ.get("LOAD_DURATION_S", "600"))
    concurrency = int(os.environ.get("LOAD_CONCURRENCY", "8"))
    target = os.environ.get("LOAD_TARGET", "staging")
    smoke = Smoke()
    smoke.step("sign in", smoke.sign_in)
    smoke.step("organisation, project, API key", smoke.tenant)
    result = asyncio.run(run(smoke.api, smoke.key, rate, duration, concurrency))
    smoke.step("revoke the load key", smoke.revoke)
    write(render(result, target, rate, duration, concurrency))
    print(json.dumps(result, indent=2))
    return 0  # §13: the result is recorded whether or not it meets the target


if __name__ == "__main__":
    sys.exit(main())
