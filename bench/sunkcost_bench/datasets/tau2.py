"""τ²-bench (sierra-research/tau2-bench, MIT): customer-service agents with a simulated user.

Each results file holds one agent model on one domain, 4 trials per task. Every assistant turn
records its USD cost, token usage and timestamp; every tool result records an error flag.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sunkcost.core import StepRecord, datetime_to_us
from sunkcost_bench.common import CACHE, CompactRun, cached_runs, fetch, hash_args, money

COMMIT = "b7ea9074c1cba482b30687fecdb5c8425fd6f619"
BASE_URL = f"https://raw.githubusercontent.com/sierra-research/tau2-bench/{COMMIT}/data/tau2/results/final/"
USER_MODEL = "gpt-4.1-2025-04-14"
AGENTS = {  # agent model -> file-name mode segment
    "claude-3-7-sonnet-20250219": "default",
    "gpt-4.1-2025-04-14": "default",
    "gpt-4.1-mini-2025-04-14": "base",
    "o4-mini-2025-04-16": "default",
}
DOMAINS = ("retail", "telecom", "airline")
# git blob ids at COMMIT, so a truncated or altered download is detected.
BLOBS = {
    "claude-3-7-sonnet-20250219_airline": "aa5ff43544e0fb3bd49ce682ba9c0d7bad10e74a",
    "claude-3-7-sonnet-20250219_retail": "1b0e345db5bd9ae5cecfc70ff399f590be70c4f8",
    "claude-3-7-sonnet-20250219_telecom": "25242a9374cc025c219fbd7b8eec94d48acd8ac5",
    "gpt-4.1-2025-04-14_airline": "f4847076cbf6e509098d22fbf26ff26a7f2b4603",
    "gpt-4.1-2025-04-14_retail": "b30d695834f16ddbb114e254e9fc35376d819f6b",
    "gpt-4.1-2025-04-14_telecom": "bce33ce98057cb58d22815d328396d41bf9ef1ba",
    "gpt-4.1-mini-2025-04-14_airline": "cfd3fc81927dab2ed942251ee5b4066e6a78cb1c",
    "gpt-4.1-mini-2025-04-14_retail": "8a55836aa5e9ed735c9900c0e357b1dd80f71c28",
    "gpt-4.1-mini-2025-04-14_telecom": "dbf2417f7878315b80fc9c9605a9dbf93fe04fd6",
    "o4-mini-2025-04-16_airline": "83f3a42988689f8dd1c02c2b4dd7119b2af438a8",
    "o4-mini-2025-04-16_retail": "bfd1be3546169e95d47f49c8b6fdf59e93a20188",
    "o4-mini-2025-04-16_telecom": "17d091253fb096b00dfa62a1e8858cdbdae1e007",
}


def file_name(agent: str, domain: str) -> str:
    return f"{agent}_{domain}_{AGENTS[agent]}_{USER_MODEL}_4trials.json"


def _us(ts: str) -> int:
    # Timestamps are naive ISO-8601; the runs were recorded in UTC.
    return datetime_to_us(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc))


def convert(sim: dict[str, Any], agent: str, domain: str) -> CompactRun | None:
    """One simulation → one compact run of the agent's own steps, or None if unlabelled."""
    reward = sim["reward_info"]["reward"]
    if reward not in (0.0, 1.0):
        return None
    steps: list[StepRecord] = []
    costs = []
    calls: dict[str, tuple[str, Any]] = {}
    for m in sim["messages"]:
        role = m["role"]
        if role == "assistant":
            usage = m.get("usage")
            if usage is None and not m.get("cost"):
                continue  # the canned greeting: no model call happened
            usage = usage or {}
            for call in m.get("tool_calls") or []:
                calls[call["id"]] = (call["name"], call.get("arguments"))
            cost = money(m.get("cost"))
            steps.append(
                StepRecord(
                    "model",
                    agent,
                    None,
                    None,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    float(cost),
                    False,
                    _us(m["timestamp"]),
                )
            )
            costs.append(cost)
        elif role == "tool" and m.get("requestor") == "assistant":
            # Tool calls made by the simulated user (telecom) are not the agent's steps.
            name, arguments = calls.get(m["id"], (None, None))
            steps.append(
                StepRecord(
                    "tool",
                    None,
                    name,
                    None if arguments is None else hash_args(arguments),
                    0,
                    0,
                    0.0,
                    bool(m.get("error")),
                    _us(m["timestamp"]),
                )
            )
            costs.append(money(0))
    return CompactRun(
        run_id=f"tau2/{domain}/{agent}/{sim['id']}",
        batch=agent,
        group=f"{domain}/{sim['task_id']}",
        started_at_us=_us(sim["start_time"]),
        outcome="success" if reward == 1.0 else "failure",
        steps=tuple(steps),
        costs=tuple(costs),
    )


def load(domain: str) -> list[CompactRun]:
    import json

    def build() -> list[CompactRun]:
        runs: list[CompactRun] = []
        for agent in AGENTS:
            name = file_name(agent, domain)
            path = fetch(
                BASE_URL + name, CACHE / "raw" / "tau2" / name, blob_sha1=BLOBS[f"{agent}_{domain}"]
            )
            doc = json.loads(path.read_text("utf-8"))
            runs.extend(
                r for s in doc["simulations"] if (r := convert(s, agent, domain)) is not None
            )
        return runs

    return cached_runs(CACHE / "runs" / f"tau2-{domain}.jsonl.gz", build)
