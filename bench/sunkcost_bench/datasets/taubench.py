"""τ-bench (sierra-research/tau-bench, MIT): the historical trajectories shipped in the repository.

They record rewards and full tool calls but no token counts, costs or timestamps. Per
ARCHITECTURE.md §4 C10, step count is then the cost proxy: every step costs one unit.
"""

from __future__ import annotations

import json
from typing import Any

from sunkcost.core import StepRecord
from sunkcost_bench.common import (
    ARRIVAL_EPOCH_US,
    CACHE,
    CompactRun,
    cached_runs,
    fetch,
    hash_args,
    money,
)

COMMIT = "59a200c6d575d595120f1cb70fea53cef0632f6b"
BASE_URL = (
    f"https://raw.githubusercontent.com/sierra-research/tau-bench/{COMMIT}/historical_trajectories/"
)
AGENTS = {"gpt-4o": "gpt-4o", "sonnet-35-new": "claude-3-5-sonnet-20241022"}
UNIT = money(1)
BLOBS = {  # git blob ids at COMMIT
    "gpt-4o-airline.json": "a5f0d91569e740b949b37a181993b3f1822a86f4",
    "gpt-4o-retail.json": "27f588d9157513c3ad11de3db9e824021152d580",
    "sonnet-35-new-airline.json": "303ee85957d7c49d73a438179f35f062e51a1491",
    "sonnet-35-new-retail.json": "f2296500688950bac6c54578752ef134961aee06",
}


def _tool_calls(m: dict[str, Any]) -> list[tuple[str, str | None, Any]]:
    out = []
    for call in m.get("tool_calls") or []:
        fn = call.get("function") or {}
        out.append((call.get("id"), fn.get("name"), fn.get("arguments")))
    return out


def convert(
    record: dict[str, Any], agent: str, domain: str, started_at_us: int
) -> CompactRun | None:
    reward = record["reward"]
    if reward not in (0.0, 1.0):
        return None
    steps: list[StepRecord] = []
    calls: dict[str, tuple[str | None, Any]] = {}
    for m in record["traj"]:
        if m.get("role") == "assistant":
            for call_id, name, arguments in _tool_calls(m):
                calls[call_id] = (name, arguments)
            steps.append(
                StepRecord("model", agent, None, None, 0, 0, float(UNIT), False, started_at_us)
            )
        elif m.get("role") == "tool":
            name, arguments = calls.get(m.get("tool_call_id"), (m.get("name"), None))
            content = m.get("content")
            error = isinstance(content, str) and content.startswith("Error")
            args = None if arguments is None else hash_args(arguments)
            steps.append(
                StepRecord("tool", None, name, args, 0, 0, float(UNIT), error, started_at_us)
            )
    return CompactRun(
        run_id=f"tau-bench/{domain}/{agent}/{record['task_id']}/{record['trial']}",
        batch=agent,
        group=f"{domain}/{record['task_id']}",
        started_at_us=started_at_us,
        outcome="success" if reward == 1.0 else "failure",
        steps=tuple(steps),
        costs=(UNIT,) * len(steps),
    )


def load(domain: str) -> list[CompactRun]:
    def build() -> list[CompactRun]:
        runs: list[CompactRun] = []
        for key, agent in AGENTS.items():
            name = f"{key}-{domain}.json"
            path = fetch(BASE_URL + name, CACHE / "raw" / "tau-bench" / name, blob_sha1=BLOBS[name])
            records = json.loads(path.read_text("utf-8"))
            # No timestamps: order runs as trial-major, task-minor (the order τ²-bench executes in).
            records.sort(key=lambda r: (r["trial"], int(r["task_id"])))
            for rank, record in enumerate(records):
                run = convert(record, agent, domain, ARRIVAL_EPOCH_US + rank * 1_000_000)
                if run is not None:
                    runs.append(run)
        return runs

    return cached_runs(CACHE / "runs" / f"tau-bench-{domain}.jsonl.gz", build)
