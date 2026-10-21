"""API keys (SDKs, business systems) and Supabase user JWTs (console). §5.1, §8."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Request

from sunkcost_api.db import Conn, Database, one
from sunkcost_api.problems import Problem

KEY_FORMAT = re.compile(r"sc_live_([a-z0-9]{8})_([a-z0-9]{32})")
_ALPHABET = string.ascii_lowercase + string.digits
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
LAST_USED_EVERY_S = 60.0


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: str
    org_id: str
    project_id: str
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class User:
    id: str


def new_api_key() -> tuple[str, str, str]:
    """Returns (full key shown once, prefix, sha256 hex of the full key)."""
    prefix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    secret = "".join(secrets.choice(_ALPHABET) for _ in range(32))
    key = f"sc_live_{prefix}_{secret}"
    return key, prefix, hashlib.sha256(key.encode("ascii")).hexdigest()


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Problem(
            401,
            "missing-credentials",
            "send Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


class KeyAuthenticator:
    """Looks a key up by prefix through ``resolve_api_key`` and compares hashes in constant time."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._last_touch: dict[str, float] = {}
        self._lock = threading.Lock()

    async def authenticate(self, request: Request, scope: str) -> ApiKey:
        token = _bearer(request)
        m = KEY_FORMAT.fullmatch(token)
        if m is None:
            raise Problem(401, "invalid-api-key", "malformed API key")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        async with self._db.tx() as conn:
            row = await one(conn, "select * from public.resolve_api_key(%s)", (m.group(1),))
        if row is None or not hmac.compare_digest(row["secret_sha256"], digest):
            raise Problem(401, "invalid-api-key", "unknown API key")
        if row["revoked_at"] is not None:
            raise Problem(401, "revoked-api-key", "this API key has been revoked")
        if scope not in row["scopes"]:
            raise Problem(403, "insufficient-scope", f"this key lacks the {scope!r} scope")
        key = ApiKey(
            str(row["id"]), str(row["org_id"]), str(row["project_id"]), tuple(row["scopes"])
        )
        await self._touch(key)
        return key

    async def _touch(self, key: ApiKey) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_touch.get(key.id, -1e9) < LAST_USED_EVERY_S:
                return
            self._last_touch[key.id] = now
        async with self._db.tx(key.org_id) as conn:
            await conn.execute(
                "update public.api_keys set last_used_at = now() where id = %s", (key.id,)
            )


class JwtAuthenticator:
    """Verifies Supabase access tokens via the JWKS endpoint (asymmetric) or the old secret."""

    def __init__(self, *, jwks_url: str | None, secret: str | None) -> None:
        if not jwks_url and not secret:
            raise RuntimeError("set SUPABASE_URL / SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET")
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600) if jwks_url else None
        self._secret = secret

    def verify(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        if alg == "HS256":
            if self._secret is None:
                raise jwt.InvalidTokenError("HS256 tokens are not accepted")
            key: Any = self._secret
        elif alg in ("ES256", "RS256") and self._jwks is not None:
            key = self._jwks.get_signing_key_from_jwt(token).key
        else:
            raise jwt.InvalidTokenError(f"unsupported alg {alg!r}")
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
        return claims

    def user(self, request: Request) -> User:
        token = _bearer(request)
        try:
            claims = self.verify(token)
        except (jwt.PyJWTError, ValueError) as exc:
            raise Problem(
                401, "invalid-token", str(exc), headers={"WWW-Authenticate": "Bearer"}
            ) from None
        if claims.get("role") != "authenticated":
            raise Problem(403, "not-a-user", "a signed-in user token is required")
        return User(str(claims["sub"]))


async def require_role(conn: Conn, org_id: str, user: User, minimum: str) -> str:
    """The caller's role in ``org_id`` (the transaction must act for that org)."""
    row = await one(
        conn,
        "select role from public.org_members where org_id = %s and user_id = %s",
        (org_id, user.id),
    )
    if row is None:
        raise Problem(404, "org-not-found", "no such organisation, or you are not a member")
    if ROLE_RANK[row["role"]] < ROLE_RANK[minimum]:
        raise Problem(403, "insufficient-role", f"requires role {minimum} or higher")
    return str(row["role"])
