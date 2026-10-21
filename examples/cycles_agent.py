"""Sunkcost in front of Cycles budget reservations: on ABORT, no reservation is made.

    uv run python examples/cycles_agent.py

Offline: the Cycles API is replaced by an in-process ``httpx.MockTransport`` that counts
reservations, and the bundle is signed with the public RFC 8032 test key, which only this example
trusts.
"""

from __future__ import annotations

import itertools
import json
import time
from typing import Any

import httpx
from runcycles import CyclesClient, CyclesConfig, cycles

from sunkcost.testing import demo_client
from sunkcost_cycles import bind_run, guard

WORKLOAD = "ops.triage_alert"
reservations: list[str] = []
_ids = itertools.count(1)


def fake_cycles_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/reservations":
        rid = f"res-{next(_ids)}"
        reservations.append(rid)
        body: dict[str, Any] = {
            "decision": "ALLOW",
            "reservation_id": rid,
            "affected_scopes": ["tenant:demo"],
            "expires_at_ms": int(time.time() * 1000) + 60_000,
            "remaining_ttl_ms": 60_000,
        }
        return httpx.Response(200, json=body)
    if path.endswith("/commit"):
        actual = json.loads(request.content)["actual"]
        return httpx.Response(200, json={"status": "COMMITTED", "charged": actual})
    if path.endswith("/extend"):
        return httpx.Response(
            200,
            json={
                "status": "ACTIVE",
                "expires_at_ms": int(time.time() * 1000) + 60_000,
                "remaining_ttl_ms": 60_000,
            },
        )
    if path.endswith("/release"):
        return httpx.Response(
            200, json={"status": "RELEASED", "released": {"unit": "USD_MICROCENTS", "amount": 0}}
        )
    return httpx.Response(404, json={"error": "NOT_FOUND", "message": path})


config = CyclesConfig(
    base_url="http://cycles.invalid", api_key="demo", tenant="demo", journal_enabled=False
)
client = CyclesClient(config)
client._http = httpx.Client(
    base_url=config.base_url, transport=httpx.MockTransport(fake_cycles_api)
)


@guard(kind="tool", name="restart_service", usage=lambda r: {"tool_args": {"service": "api"}})
@cycles(  # type: ignore[untyped-decorator]  # runcycles ships no type information
    estimate=500_000, action_kind="tool.call", action_name="restart_service", client=client
)
def restart_service() -> bool:
    return False  # the restart never fixes the alert


def main() -> int:
    sl = demo_client(
        WORKLOAD,
        weights={"repeat_action_rate": "6.0"},
        intercept="-4.0",
        threshold="0.8",
        min_steps=2,
    )
    run = sl.start_run(WORKLOAD)
    with bind_run(run):
        try:
            for _ in range(20):
                restart_service()
            run.end("completed")
        except Exception as exc:  # RunAborted, raised before Cycles reserves anything
            run.end("aborted")
            print(f"{type(exc).__name__}: {exc}")
    print(f"{len(reservations)} reservations made for {run.steps} steps")
    sl.close()
    return len(reservations)


if __name__ == "__main__":
    main()
