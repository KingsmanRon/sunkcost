"""Console endpoints (Supabase user JWT). Every mutation writes ``audit_log`` (§4 C6, §8).

Requests name their organisation in the ``x-sunkcost-org`` header, so the transaction can act for
that org under RLS before any row is read (ADR-0005).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from sunkcost._ids import uuid7
from sunkcost_api.auth import new_api_key, require_role
from sunkcost_api.common import (
    audit,
    enqueue,
    jsonable,
    org_header,
    parse,
    read_json,
    services,
    user,
)
from sunkcost_api.db import one, required
from sunkcost_api.problems import Problem

router = APIRouter()

WORKLOAD_COLS = (
    "id, org_id, project_id, name, mode, override, target_recall, delta, min_steps, "
    "holdout_rate, min_savings_share, created_at, updated_at"
)
SETTINGS = ("mode", "target_recall", "delta", "min_steps", "holdout_rate", "min_savings_share")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrgIn(Body):
    name: str = Field(min_length=1, max_length=256)


class ProjectIn(Body):
    name: str = Field(min_length=1, max_length=256)


class ApiKeyIn(Body):
    project_id: uuid.UUID
    name: str = Field(default="", max_length=256)
    scopes: list[Literal["ingest", "outcome:write", "bundle:read"]] = Field(min_length=1)


class WorkloadSettings(Body):
    mode: Literal["shadow", "enforce"] | None = None
    target_recall: Decimal | None = Field(default=None, gt=0, lt=1, decimal_places=4)
    delta: Decimal | None = Field(default=None, gt=0, lt=1, decimal_places=4)
    min_steps: int | None = Field(default=None, ge=0, le=1_000_000)
    holdout_rate: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    min_savings_share: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)


class WorkloadIn(WorkloadSettings):
    project_id: uuid.UUID
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OverrideIn(Body):
    override: Literal["shadow"] | None


@router.post("/v1/orgs", status_code=201)
async def create_org(request: Request) -> dict[str, Any]:
    u = await user(request)
    body = parse(OrgIn, await read_json(request))
    org_id = uuid7()  # chosen here, so the transaction acts for the new org from the start
    async with services(request).db.tx(org_id) as conn:
        row = await required(
            conn,
            "insert into public.orgs (id, name) values (%s, %s) returning id, name, created_at",
            (org_id, body.name),
        )
        await conn.execute(
            "insert into public.org_members (org_id, user_id, role) values (%s, %s, 'owner')",
            (org_id, u.id),
        )
        await audit(conn, org_id, u.id, "org.create", f"org:{org_id}", after=row)
    return jsonable(row)


@router.post("/v1/projects", status_code=201)
async def create_project(request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    body = parse(ProjectIn, await read_json(request))
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        row = await one(
            conn,
            "insert into public.projects (org_id, name) values (%s, %s) "
            "on conflict (org_id, name) do nothing returning id, org_id, name, created_at",
            (org_id, body.name),
        )
        if row is None:
            raise Problem(409, "project-exists", "a project with this name already exists")
        await audit(conn, org_id, u.id, "project.create", f"project:{row['id']}", after=row)
    return jsonable(row)


@router.post("/v1/api-keys", status_code=201)
async def create_api_key(request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    body = parse(ApiKeyIn, await read_json(request))
    secret, prefix, digest = new_api_key()
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        project_id = str(body.project_id)
        if await one(conn, "select 1 from public.projects where id = %s", (project_id,)) is None:
            raise Problem(404, "project-not-found", "no such project in this organisation")
        row = await required(
            conn,
            "insert into public.api_keys (org_id, project_id, name, prefix, secret_sha256, scopes, "
            "created_by) values (%s, %s, %s, %s, %s, %s, %s) "
            "returning id, project_id, name, prefix, scopes, created_at",
            (org_id, project_id, body.name, prefix, digest, sorted(set(body.scopes)), u.id),
        )
        await audit(conn, org_id, u.id, "api_key.create", f"api_key:{row['id']}", after=row)
    return {**jsonable(row), "key": secret}  # the secret is shown exactly once


@router.delete("/v1/api-keys/{key_id}")
async def revoke_api_key(key_id: uuid.UUID, request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        row = await one(
            conn,
            "update public.api_keys set revoked_at = coalesce(revoked_at, now()) where id = %s "
            "returning id, prefix, revoked_at",
            (str(key_id),),
        )
        if row is None:
            raise Problem(404, "api-key-not-found", "no such API key in this organisation")
        await audit(conn, org_id, u.id, "api_key.revoke", f"api_key:{key_id}", after=row)
    return jsonable(row)


@router.post("/v1/workloads", status_code=201)
async def create_workload(request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    body = parse(WorkloadIn, await read_json(request))
    chosen = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in SETTINGS}
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        project_id = str(body.project_id)
        if await one(conn, "select 1 from public.projects where id = %s", (project_id,)) is None:
            raise Problem(404, "project-not-found", "no such project in this organisation")
        cols = ["org_id", "project_id", "name", *chosen]  # names come from SETTINGS only
        sql = (
            f"insert into public.workloads ({', '.join(cols)}) "  # noqa: S608
            f"values ({', '.join(['%s'] * len(cols))}) "
            f"on conflict (project_id, name) do nothing returning {WORKLOAD_COLS}"
        )
        row = await one(conn, sql, [org_id, project_id, body.name, *chosen.values()])
        if row is None:
            raise Problem(409, "workload-exists", "a workload with this name already exists")
        await audit(conn, org_id, u.id, "workload.create", f"workload:{row['id']}", after=row)
    return jsonable(row)


@router.patch("/v1/workloads/{workload_id}")
async def update_workload(workload_id: uuid.UUID, request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    changes = parse(WorkloadSettings, await read_json(request)).model_dump(exclude_none=True)
    if not changes:
        raise Problem(422, "nothing-to-change", "send at least one setting")
    wid = str(workload_id)
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        select = f"select {WORKLOAD_COLS} from public.workloads where id = %s for update"  # noqa: S608
        before = await one(conn, select, (wid,))
        if before is None:
            raise Problem(404, "workload-not-found", "no such workload in this organisation")
        sets = ", ".join(f"{c} = %s" for c in changes)  # names come from WorkloadSettings only
        sql = (
            f"update public.workloads set {sets}, updated_at = now() "  # noqa: S608
            f"where id = %s returning {WORKLOAD_COLS}"
        )
        after = await required(conn, sql, [*changes.values(), wid])
        job_id = None
        if "mode" in changes and changes["mode"] != before["mode"]:
            # A mode change needs a newly signed bundle, and only the worker signs.
            payload = {"workload_id": wid, "mode": changes["mode"]}
            job_id = await enqueue(conn, org_id, "resign_bundle", payload)
        target = f"workload:{wid}"
        await audit(conn, org_id, u.id, "workload.update", target, before=before, after=after)
    return {**jsonable(after), "resign_job_id": job_id}


@router.post("/v1/workloads/{workload_id}/calibrate", status_code=202)
async def calibrate(workload_id: uuid.UUID, request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    wid = str(workload_id)
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "member")
        if await one(conn, "select 1 from public.workloads where id = %s", (wid,)) is None:
            raise Problem(404, "workload-not-found", "no such workload in this organisation")
        job_id = await enqueue(conn, org_id, "calibrate", {"workload_id": wid})
        target = f"workload:{wid}"
        await audit(conn, org_id, u.id, "workload.calibrate", target, after={"job_id": job_id})
    return {"job_id": job_id, "status": "queued"}


@router.post("/v1/bundles/{bundle_id}/activate")
async def activate(bundle_id: uuid.UUID, request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    bid = str(bundle_id)
    async with services(request).db.tx(org_id) as conn:
        await require_role(conn, org_id, u, "admin")
        bundle = await one(
            conn,
            "select id, workload_id, version, status from public.policy_bundles "
            "where id = %s for update",
            (bid,),
        )
        if bundle is None:
            raise Problem(404, "bundle-not-found", "no such bundle in this organisation")
        if bundle["status"] == "active":
            return jsonable(bundle)
        if bundle["status"] != "validated":
            detail = f"only validated bundles can be activated (this one is {bundle['status']})"
            raise Problem(409, "bundle-not-validated", detail)
        await conn.execute(
            "update public.policy_bundles set status = 'retired' "
            "where workload_id = %s and status = 'active'",
            (bundle["workload_id"],),
        )
        after = await required(
            conn,
            "update public.policy_bundles set status = 'active', activated_at = now() "
            "where id = %s returning id, workload_id, version, status, activated_at",
            (bid,),
        )
        target = f"bundle:{bid}"
        await audit(conn, org_id, u.id, "bundle.activate", target, before=bundle, after=after)
    return jsonable(after)


@router.post("/v1/workloads/{workload_id}/override")
async def override(workload_id: uuid.UUID, request: Request) -> dict[str, Any]:
    u = await user(request)
    org_id = org_header(request)
    body = parse(OverrideIn, await read_json(request))
    wid = str(workload_id)
    async with services(request).db.tx(org_id) as conn:
        # Setting the override can only reduce enforcement (member); clearing it restores the
        # signed mode, which needs admin (ADR-0005).
        await require_role(conn, org_id, u, "member" if body.override == "shadow" else "admin")
        before = await one(
            conn, "select id, override from public.workloads where id = %s for update", (wid,)
        )
        if before is None:
            raise Problem(404, "workload-not-found", "no such workload in this organisation")
        after = await required(
            conn,
            "update public.workloads set override = %s, updated_at = now() where id = %s "
            "returning id, override",
            (body.override, wid),
        )
        target = f"workload:{wid}"
        await audit(conn, org_id, u.id, "workload.override", target, before=before, after=after)
    return jsonable(after)
