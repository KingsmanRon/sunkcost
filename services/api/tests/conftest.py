"""Shared harness for the database gates (G5 HTTP part, G6): a real local Supabase stack.

Nothing here is mocked. The API runs under uvicorn on 127.0.0.1 as role ``sunkcost_api``. Users
sign up through Supabase Auth and use real access tokens. The superuser connection is used only
to seed fixtures that later phases create through the worker (signed bundles).

If the stack is not running, the tests fail rather than skip: a database gate that did not run
must never look like a pass.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
import uvicorn
from psycopg.rows import dict_row

from sunkcost_api.app import create_app
from sunkcost_api.config import Settings

SUPABASE_URL = os.environ.get("SUNKCOST_TEST_SUPABASE_URL", "http://127.0.0.1:54321")
ADMIN_DB_URL = os.environ.get(
    "SUNKCOST_TEST_ADMIN_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
API_DB_URL = os.environ.get(
    "SUNKCOST_TEST_API_DB_URL",
    "postgresql://sunkcost_api:sunkcost_api_local@127.0.0.1:54322/postgres",
)


def publishable_key() -> str:
    """The local stack's public (anon) key, from `supabase status` (it is not a secret)."""
    env = os.environ.get("SUNKCOST_TEST_ANON_KEY")
    if env:
        return env
    out = subprocess.run(
        ["supabase", "status", "-o", "json"],  # noqa: S607 - the CLI is on PATH by design
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    doc = json.loads(out[out.index("{") :])
    return str(doc.get("ANON_KEY") or doc["PUBLISHABLE_KEY"])


def require_stack() -> None:
    try:
        httpx.get(f"{SUPABASE_URL}/auth/v1/health", timeout=5).raise_for_status()
        with psycopg.connect(ADMIN_DB_URL, connect_timeout=5) as conn:
            conn.execute("select 1")
    except Exception as exc:  # pragma: no cover - environment problem
        raise RuntimeError(
            "local Supabase is not running (supabase start); the DB gates cannot run"
        ) from exc


@dataclass
class Account:
    user_id: str
    token: str
    email: str

    def headers(self, org_id: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}"}
        if org_id:
            h["x-sunkcost-org"] = org_id
        return h


def sign_up(anon_key: str) -> Account:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers={"apikey": anon_key},
        json={"email": email, "password": f"pw-{uuid.uuid4().hex}"},
        timeout=10,
    )
    r.raise_for_status()
    doc = r.json()
    return Account(doc["user"]["id"], doc["access_token"], email)


def admin_execute(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(ADMIN_DB_URL, row_factory=dict_row) as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ApiServer:
    def __init__(self, **overrides: Any) -> None:
        settings = Settings(
            database_url=API_DB_URL,
            supabase_url=SUPABASE_URL,
            supabase_jwt_secret=None,
            supabase_jwks_url=f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            console_origin="http://localhost:3000",
            **overrides,
        )
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> ApiServer:
        self._thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.url}/readyz", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("API did not become ready")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(10)


@dataclass
class Tenant:
    account: Account
    org_id: str
    project_id: str
    key: str  # ingest + outcome:write + bundle:read


def make_tenant(api: str, anon_key: str) -> Tenant:
    account = sign_up(anon_key)
    with httpx.Client(base_url=api, timeout=10) as c:
        org = c.post("/v1/orgs", json={"name": "org"}, headers=account.headers()).json()
        project = c.post(
            "/v1/projects", json={"name": "main"}, headers=account.headers(org["id"])
        ).json()
        key = c.post(
            "/v1/api-keys",
            json={
                "project_id": project["id"],
                "scopes": ["ingest", "outcome:write", "bundle:read"],
            },
            headers=account.headers(org["id"]),
        ).json()
    return Tenant(account, org["id"], project["id"], key["key"])


def iter_problems(responses: list[httpx.Response]) -> Iterator[dict[str, Any]]:
    for r in responses:
        yield r.json()


# -- fixtures -----------------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def anon_key() -> str:
    require_stack()
    return publishable_key()


@pytest.fixture(scope="session")
def api(anon_key: str) -> Iterator[str]:
    with ApiServer() as server:
        yield server.url


@pytest.fixture
def tenant(api: str, anon_key: str) -> Tenant:
    return make_tenant(api, anon_key)


@pytest.fixture
def other_tenant(api: str, anon_key: str) -> Tenant:
    return make_tenant(api, anon_key)


@pytest.fixture(name="db_admin")
def db_admin_fixture() -> Any:
    """Superuser SQL for fixtures and assertions only (never used by any component)."""
    require_stack()
    return admin_execute


@pytest.fixture(name="new_user")
def new_user_fixture(anon_key: str) -> Any:
    return lambda: sign_up(anon_key)
