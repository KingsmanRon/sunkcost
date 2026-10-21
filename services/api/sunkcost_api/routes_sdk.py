"""Endpoints for SDKs and business systems (API keys): ingest, outcomes, the active bundle."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from pydantic import ValidationError

from sunkcost_api import ingest as ingest_mod
from sunkcost_api.common import api_key, parse, read_json, services
from sunkcost_api.db import one
from sunkcost_api.problems import Problem
from sunkcost_spec import models

router = APIRouter()

EVENTS = Counter("sunkcost_ingest_events_total", "Ingested events", ["result"])
DROPPED = Counter("sunkcost_ingest_sdk_dropped_events_total", "Events SDKs report having dropped")

RUN_BY_REF_SQL = (
    "select r.id from public.runs r join public.workloads w on w.id = r.workload_id "
    "where w.project_id = %s and w.name = %s and r.external_ref = %s "
    "order by r.started_at desc nulls last limit 1"
)
ACTIVE_BUNDLE_SQL = (
    "select b.id, b.payload, b.signature, b.key_id, w.override from public.workloads w "
    "join public.policy_bundles b on b.workload_id = w.id and b.status = 'active' "
    "where w.project_id = %s and w.name = %s"
)


def bundle_etag(bundle_id: str, signature: str, override: str | None) -> str:
    """Bundle id, signature and override state: a re-signed bundle or an override changes it."""
    material = f"{bundle_id}|{signature}|{override or ''}".encode()
    return '"' + hashlib.sha256(material).hexdigest()[:32] + '"'


@router.post("/v1/ingest")
async def ingest(request: Request) -> dict[str, Any]:
    key = await api_key(request, "ingest")
    doc = await read_json(request)
    try:
        header, events = ingest_mod.validate_envelope(doc)
    except (ValueError, ValidationError) as exc:
        raise Problem(400, "invalid-batch", str(exc)[:500]) from None
    actor = f"api_key:{key.id}"
    async with services(request).db.tx(key.org_id) as conn:
        result = await ingest_mod.ingest(conn, key.org_id, key.project_id, events, actor=actor)
    EVENTS.labels("accepted").inc(result.accepted)
    EVENTS.labels("duplicate").inc(result.duplicates)
    EVENTS.labels("rejected").inc(len(result.rejected))
    if header.dropped_events:
        DROPPED.inc(header.dropped_events)
    return result.to_json()


@router.post("/v1/outcomes")
async def outcomes(request: Request) -> dict[str, Any]:
    key = await api_key(request, "outcome:write")
    body = parse(models.OutcomeBody, await read_json(request))
    async with services(request).db.tx(key.org_id) as conn:
        if body.run_id is not None:
            run_id = body.run_id
            try:
                await ingest_mod.ensure_run(conn, key.org_id, key.project_id, run_id)
            except ingest_mod.EventRejectedError as exc:
                raise Problem(409, "wrong-project", str(exc)) from None
        else:
            params = (key.project_id, body.workload, body.external_ref)
            row = await one(conn, RUN_BY_REF_SQL, params)
            if row is None:
                raise Problem(404, "run-not-found", "no run with this workload and external_ref")
            run_id = str(row["id"])
        status = await ingest_mod.apply_outcome(
            conn, key.org_id, run_id, body.outcome, body.source, f"api_key:{key.id}"
        )
    return {"run_id": run_id, "status": status}


@router.get("/v1/bundles/active")
async def active_bundle(request: Request, workload: str) -> Response:
    key = await api_key(request, "bundle:read")
    async with services(request).db.tx(key.org_id) as conn:
        row = await one(conn, ACTIVE_BUNDLE_SQL, (key.project_id, workload))
    if row is None:
        raise Problem(404, "no-active-bundle", "this workload has no active bundle")
    etag = bundle_etag(str(row["id"]), row["signature"], row["override"])
    wanted = [t.strip() for t in request.headers.get("if-none-match", "").split(",")]
    if etag in wanted:
        return Response(status_code=304, headers={"ETag": etag})
    doc = {
        "schema_version": "1",
        "bundle": row["payload"],
        "key_id": row["key_id"],
        "signature": row["signature"],
        "override": row["override"],
    }
    return JSONResponse(doc, headers={"ETag": etag, "Cache-Control": "no-cache"})
