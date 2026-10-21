"""Idempotent ingestion (ARCHITECTURE.md §8.2, ADR-0005).

The batch header must be valid as a whole. Each event is then validated and applied on its own,
inside a savepoint, so one bad event is reported in ``rejected`` without failing the batch.

* ``run_start``: upsert; the first write wins. It fills a placeholder run.
* ``step``: ``INSERT … ON CONFLICT DO NOTHING``; a conflict counts as a duplicate.
* ``run_end``: sets the end fields once.
* ``outcome``: the first final outcome wins. A different later value sets ``outcome_conflict``
  and writes an ``audit_log`` entry.
* An event for an unknown run creates a placeholder run (status ``partial``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter, ValidationError

from sunkcost.core import parse_timestamp_us
from sunkcost_api.db import Conn, one, required
from sunkcost_spec import models

EVENT: TypeAdapter[Any] = TypeAdapter(models.Event)

RUN_SQL = (
    "select project_id, workload_id, run_month, ended_at, outcome from public.runs "
    "where org_id = %s and id = %s"
)
PLACEHOLDER_SQL = (
    "insert into public.runs (org_id, id, project_id) values (%s, %s, %s) on conflict do nothing"
)
WORKLOAD_INSERT_SQL = (
    "insert into public.workloads (org_id, project_id, name) values (%s, %s, %s) "
    "on conflict (project_id, name) do nothing returning id"
)
WORKLOAD_SQL = "select id from public.workloads where project_id = %s and name = %s"
RUN_START_SQL = (
    "insert into public.runs (org_id, id, project_id, workload_id, external_ref, started_at, mode, "
    "holdout, bundle_id, bundle_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "on conflict (org_id, id) do update set workload_id = excluded.workload_id, "
    "external_ref = excluded.external_ref, started_at = excluded.started_at, mode = excluded.mode, "
    "holdout = excluded.holdout, bundle_id = excluded.bundle_id, "
    "bundle_version = excluded.bundle_version "
    "where runs.workload_id is null and runs.project_id = excluded.project_id returning 1"
)
STEP_SQL = (
    "insert into public.step_events (org_id, run_id, seq, run_month, ts, kind, model, tool_name, "
    "args_hash, input_tokens, output_tokens, cost_usd, error, latency_ms, action, score, "
    "threshold, "
    "would_abort, reason) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "%s, %s, %s) on conflict do nothing returning 1"
)
RUN_END_SQL = (
    "update public.runs set ended_at = %s, end_status = %s, steps = %s, total_cost_usd = %s, "
    "would_abort_step = %s, cost_through_would_abort = %s, aborted_step = %s "
    "where org_id = %s and id = %s and ended_at is null returning 1"
)
OUTCOME_SQL = (
    "update public.runs set outcome = %s, outcome_source = %s, outcome_at = now() "
    "where org_id = %s and id = %s and outcome is null returning 1"
)
CONFLICT_SQL = "update public.runs set outcome_conflict = true where org_id = %s and id = %s"
AUDIT_CONFLICT_SQL = (
    "insert into public.audit_log (org_id, actor, action, target, before, after) "
    "values (%s, %s, 'outcome_conflict', %s, %s, %s)"
)


def ts(value: str) -> datetime:
    """A wire timestamp (UTC, 0-9 fractional digits) as an aware datetime, microsecond precision."""
    us = parse_timestamp_us(value)
    whole = datetime.fromtimestamp(us // 1_000_000, tz=timezone.utc)
    return whole.replace(microsecond=us % 1_000_000)


def money(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "duplicates": self.duplicates, "rejected": self.rejected}


class EventRejectedError(Exception):
    """A valid event that cannot be applied (for example, a run owned by another project)."""


class BatchHeader(models.WireModel):
    """``ingest-batch.v1`` without its events, which are validated one by one."""

    schema_version: Literal["1"]
    sent_at: models.Timestamp
    sdk: models.SdkInfo
    dropped_events: models.SafeUint


def validate_envelope(doc: Any) -> tuple[BatchHeader, list[Any]]:
    """Validate the batch header and the shape of the event list; returns both."""
    if not isinstance(doc, dict):
        raise ValueError("body must be a JSON object")
    events = doc.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 1000:
        raise ValueError("events must be an array of 1 to 1,000 events")
    header = BatchHeader.model_validate({k: v for k, v in doc.items() if k != "events"})
    return header, events


async def ensure_run(conn: Conn, org_id: str, project_id: str, run_id: str) -> dict[str, Any]:
    """The run's row, creating a placeholder if this is the first event seen for it."""
    await conn.execute(PLACEHOLDER_SQL, (org_id, run_id, project_id))
    row = await required(conn, RUN_SQL, (org_id, run_id))
    if str(row["project_id"]) != project_id:
        raise EventRejectedError("run belongs to another project")
    return row


async def workload_id(conn: Conn, org_id: str, project_id: str, name: str) -> str:
    """The workload's id, creating it with default settings on first use (ADR-0005)."""
    row = await one(conn, WORKLOAD_INSERT_SQL, (org_id, project_id, name))
    if row is None:
        row = await required(conn, WORKLOAD_SQL, (project_id, name))
    return str(row["id"])


async def apply_outcome(
    conn: Conn, org_id: str, run_id: str, outcome: str, source: str, actor: str
) -> str:
    """Returns ``recorded``, ``duplicate`` or ``conflict``."""
    if await one(conn, OUTCOME_SQL, (outcome, source, org_id, run_id)) is not None:
        return "recorded"
    row = await required(conn, RUN_SQL, (org_id, run_id))
    if row["outcome"] == outcome:
        return "duplicate"
    await conn.execute(CONFLICT_SQL, (org_id, run_id))
    before = Jsonb({"outcome": row["outcome"]})
    after = Jsonb({"outcome": outcome, "source": source})
    await conn.execute(AUDIT_CONFLICT_SQL, (org_id, actor, f"run:{run_id}", before, after))
    return "conflict"


async def _run_start(conn: Conn, org_id: str, project_id: str, e: models.RunStart) -> bool:
    wid = await workload_id(conn, org_id, project_id, e.workload)
    params = (
        org_id,
        e.run_id,
        project_id,
        wid,
        e.external_ref,
        ts(e.started_at),
        e.mode,
        e.holdout,
        e.bundle_id,
        e.bundle_version,
    )
    if await one(conn, RUN_START_SQL, params) is not None:
        return True
    existing = await one(conn, RUN_SQL, (org_id, e.run_id))
    if existing is not None and str(existing["project_id"]) != project_id:
        raise EventRejectedError("run belongs to another project")
    return False  # the run was already started: the first write wins


async def _step(conn: Conn, org_id: str, project_id: str, e: models.Step) -> bool:
    run = await ensure_run(conn, org_id, project_id, e.run_id)
    d = e.decision
    params = (
        org_id,
        e.run_id,
        e.seq,
        run["run_month"],
        ts(e.ts),
        e.kind,
        e.model,
        e.tool_name,
        e.args_hash,
        e.input_tokens,
        e.output_tokens,
        money(e.cost_usd),
        e.error,
        e.latency_ms,
        None if d is None else d.action,
        None if d is None or d.score is None else float(d.score),
        None if d is None or d.threshold is None else float(d.threshold),
        None if d is None else d.would_abort,
        None if d is None else d.reason,
    )
    return await one(conn, STEP_SQL, params) is not None


async def _run_end(conn: Conn, org_id: str, project_id: str, e: models.RunEnd) -> bool:
    await ensure_run(conn, org_id, project_id, e.run_id)
    params = (
        ts(e.ended_at),
        e.status,
        e.steps,
        money(e.total_cost_usd),
        e.would_abort_step,
        money(e.cost_through_would_abort_usd),
        e.aborted_step,
        org_id,
        e.run_id,
    )
    return await one(conn, RUN_END_SQL, params) is not None


async def apply_event(conn: Conn, org_id: str, project_id: str, event: Any, actor: str) -> bool:
    """Apply one validated event. True if it changed anything, False if it was a duplicate."""
    if isinstance(event, models.RunStart):
        return await _run_start(conn, org_id, project_id, event)
    if isinstance(event, models.Step):
        return await _step(conn, org_id, project_id, event)
    if isinstance(event, models.RunEnd):
        return await _run_end(conn, org_id, project_id, event)
    if isinstance(event, models.OutcomeEvent):
        await ensure_run(conn, org_id, project_id, event.run_id)
        status = await apply_outcome(conn, org_id, event.run_id, event.outcome, event.source, actor)
        return status != "duplicate"
    raise EventRejectedError(f"unsupported event type {type(event).__name__}")


async def ingest(
    conn: Conn, org_id: str, project_id: str, events: list[Any], actor: str
) -> IngestResult:
    result = IngestResult()
    for index, raw in enumerate(events):
        try:
            event = EVENT.validate_json(json.dumps(raw))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            reason = f"{where}: {first.get('msg', 'invalid')}"
            result.rejected.append({"index": index, "reason": reason})
            continue
        try:
            async with conn.transaction():  # savepoint: a failed event leaves the others intact
                changed = await apply_event(conn, org_id, project_id, event, actor)
        except EventRejectedError as exc:
            result.rejected.append({"index": index, "reason": str(exc)})
            continue
        if changed:
            result.accepted += 1
        else:
            result.duplicates += 1
    return result
