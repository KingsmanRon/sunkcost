"""G5 (HTTP part): tenant isolation through the real PostgREST, with real Supabase Auth tokens.

The pgTAP suite (supabase/tests) proves the same rules at the SQL level by switching roles the way
PostgREST does; this test goes through PostgREST itself. Run with `make test-db`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from sunkcost._ids import uuid7

pytestmark = pytest.mark.db
SUPABASE_URL = os.environ.get("SUNKCOST_TEST_SUPABASE_URL", "http://127.0.0.1:54321")

TABLES = [
    "orgs",
    "org_members",
    "projects",
    "api_keys",
    "workloads",
    "runs",
    "step_events",
    "policy_bundles",
    "reports",
    "jobs",
    "audit_log",
]


def rest(anon_key: str, token: str, path: str, method: str = "GET", **kw: Any) -> httpx.Response:
    return httpx.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{path}",
        timeout=10,
        headers={"apikey": anon_key, "Authorization": f"Bearer {token}"},
        **kw,
    )


@pytest.fixture
def two_orgs(new_user: Any, db_admin: Any) -> tuple[Any, Any, str, str]:
    a, b = new_user(), new_user()
    orgs = []
    for user in (a, b):
        org = uuid7()
        project = uuid7()
        workload = uuid7()
        run = uuid7()
        db_admin(
            "insert into public.orgs (id, name, webhook_secret_hash) values (%s, 'org', 'h')",
            (org,),
        )
        db_admin(
            "insert into public.org_members (org_id, user_id, role) values (%s, %s, 'owner')",
            (org, user.user_id),
        )
        db_admin(
            "insert into public.projects (id, org_id, name) values (%s, %s, 'p')", (project, org)
        )
        db_admin(
            "insert into public.api_keys (org_id, project_id, prefix, secret_sha256, scopes) "
            "values (%s, %s, %s, %s, '{ingest}')",
            (org, project, uuid7()[-8:], "0" * 64),
        )
        db_admin(
            "insert into public.workloads (id, org_id, project_id, name) values (%s, %s, %s, 'w')",
            (workload, org, project),
        )
        db_admin(
            "insert into public.runs (org_id, id, project_id, workload_id, started_at) "
            "values (%s, %s, %s, %s, now())",
            (org, run, project, workload),
        )
        db_admin(
            "insert into public.step_events "
            "(org_id, run_id, seq, run_month, ts, kind, input_tokens, "
            "output_tokens, error) select org_id, id, 1, run_month, now(), 'tool', 0, 0, false "
            "from public.runs where org_id = %s",
            (org,),
        )
        db_admin(
            "insert into public.policy_bundles "
            "(org_id, workload_id, version, status, payload, signature, "
            "key_id) values (%s, %s, 1, 'validated', '{}', 's', 'k')",
            (org, workload),
        )
        db_admin(
            "insert into public.reports (org_id, workload_id, period_start, period_end) "
            "values (%s, %s, now() - interval '1 day', now())",
            (org, workload),
        )
        db_admin("insert into public.jobs (org_id, type) values (%s, 'calibrate')", (org,))
        db_admin(
            "insert into public.audit_log (org_id, actor, action) values (%s, 'system', 'seed')",
            (org,),
        )
        orgs.append(org)
    return a, b, orgs[0], orgs[1]


def test_members_read_only_their_own_org(anon_key: str, two_orgs: Any) -> None:
    a, _, org_a, org_b = two_orgs
    for table in TABLES:
        select = "id,name" if table == "orgs" else "org_id"
        rows = rest(anon_key, a.token, f"{table}?select={select if table != 'orgs' else 'id'}")
        assert rows.status_code == 200, (table, rows.text)
        seen = {r.get("org_id", r.get("id")) for r in rows.json()}
        assert org_a in seen, table
        assert org_b not in seen, table
        # Asking for B's rows by id returns nothing, not an error that would confirm they exist.
        column = "id" if table == "orgs" else "org_id"
        assert (
            rest(anon_key, a.token, f"{table}?{column}=eq.{org_b}&select={column}").json() == []
        ), table


def test_secret_columns_are_not_readable(anon_key: str, two_orgs: Any) -> None:
    a, *_ = two_orgs
    assert rest(anon_key, a.token, "api_keys?select=secret_sha256").status_code in (401, 403)
    assert rest(anon_key, a.token, "orgs?select=webhook_secret_hash").status_code in (401, 403)
    assert rest(anon_key, a.token, "api_keys?select=prefix,scopes").status_code == 200


def test_console_role_cannot_write(anon_key: str, two_orgs: Any) -> None:
    a, _, org_a, _ = two_orgs
    attempts = [
        rest(anon_key, a.token, "projects", "POST", json={"org_id": org_a, "name": "x"}),
        rest(anon_key, a.token, f"workloads?org_id=eq.{org_a}", "PATCH", json={"mode": "enforce"}),
        rest(anon_key, a.token, f"runs?org_id=eq.{org_a}", "DELETE"),
        rest(
            anon_key,
            a.token,
            "audit_log",
            "POST",
            json={"org_id": org_a, "actor": "x", "action": "y"},
        ),
        rest(anon_key, a.token, "rpc/resolve_api_key", "POST", json={"p_prefix": "aaaaaaaa"}),
        rest(anon_key, a.token, "rpc/list_due_work", "POST", json={}),
    ]
    assert [r.status_code for r in attempts] == [403] * len(attempts), [r.text for r in attempts]


def test_anon_reads_nothing(anon_key: str, two_orgs: Any) -> None:
    for table in TABLES:
        assert rest(anon_key, anon_key, f"{table}?select=org_id").status_code == 401, table
