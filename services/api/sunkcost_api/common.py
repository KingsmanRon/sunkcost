"""Shared request plumbing: the service container, body parsing, audit and job helpers."""

from __future__ import annotations

import asyncio
import json
import uuid
import zlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from sunkcost_api.auth import ApiKey, JwtAuthenticator, KeyAuthenticator, User
from sunkcost_api.config import Settings
from sunkcost_api.db import Conn, Database, required
from sunkcost_api.problems import Problem
from sunkcost_api.ratelimit import RateLimiter

ORG_HEADER = "x-sunkcost-org"

AUDIT_SQL = (
    "insert into public.audit_log (org_id, actor, action, target, before, after) "
    "values (%s, %s, %s, %s, %s, %s)"
)
JOB_SQL = "insert into public.jobs (org_id, type, payload) values (%s, %s, %s) returning id"


@dataclass
class Services:
    settings: Settings
    db: Database
    keys: KeyAuthenticator
    jwt: JwtAuthenticator
    limiter: RateLimiter


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


async def api_key(request: Request, scope: str) -> ApiKey:
    svc = services(request)
    key = await svc.keys.authenticate(request, scope)
    wait = svc.limiter.take(key.id)
    if wait is not None:
        raise Problem(
            429,
            "rate-limited",
            "too many requests for this API key",
            headers={"Retry-After": str(max(1, round(wait)))},
        )
    return key


async def user(request: Request) -> User:
    # JWKS fetches use blocking I/O on a cache miss, so keep them off the event loop.
    return await asyncio.to_thread(services(request).jwt.user, request)


def org_header(request: Request) -> str:
    value = request.headers.get(ORG_HEADER, "")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise Problem(
            400, "missing-org", f"send the organisation id in the {ORG_HEADER} header"
        ) from None


async def read_json(request: Request) -> Any:
    """JSON body, at most ``max_body_bytes`` after decompression (gzip and deflate accepted)."""
    limit = services(request).settings.max_body_bytes
    too_large = Problem(413, "too-large", f"request bodies are limited to {limit} bytes")
    if int(request.headers.get("content-length") or 0) > limit:
        raise too_large
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise too_large
    encoding = request.headers.get("content-encoding", "identity").lower()
    if encoding in ("gzip", "deflate"):
        wbits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        d = zlib.decompressobj(wbits)
        try:
            data = d.decompress(bytes(raw), limit + 1)
        except zlib.error:
            raise Problem(400, "bad-encoding", "body is not valid gzip/deflate data") from None
        if len(data) > limit or d.unconsumed_tail:
            raise too_large
    elif encoding == "identity":
        data = bytes(raw)
    else:
        raise Problem(
            415, "unsupported-encoding", f"content-encoding {encoding!r} is not supported"
        )
    try:
        return json.loads(data)
    except ValueError:
        raise Problem(400, "invalid-json", "body is not valid JSON") from None


def parse(model: type[BaseModel], doc: Any) -> Any:
    try:
        return model.model_validate(doc)
    except ValidationError as exc:
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        raise Problem(
            422, "invalid-request", "request validation failed", extra={"errors": errors[:20]}
        ) from None


def jsonable(row: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (row or {}).items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, Decimal):
            v = format(v, "f")
        elif hasattr(v, "isoformat"):
            v = v.isoformat().replace("+00:00", "Z")
        out[k] = v
    return out


async def audit(
    conn: Conn,
    org_id: str,
    actor: str,
    action: str,
    target: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    b = None if before is None else Jsonb(jsonable(before))
    a = None if after is None else Jsonb(jsonable(after))
    await conn.execute(AUDIT_SQL, (org_id, actor, action, target, b, a))


async def enqueue(conn: Conn, org_id: str, job_type: str, payload: dict[str, Any]) -> str:
    """Hand work to the worker. The API and the worker talk only through this table."""
    row = await required(conn, JOB_SQL, (org_id, job_type, Jsonb(payload)))
    return str(row["id"])
