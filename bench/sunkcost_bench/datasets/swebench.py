"""SWE-bench Verified, mini-SWE-agent 2.0.0 "bash-only" runs by the SWE-bench maintainers.

Labels (resolved or not) come from each entry's ``per_instance_details.json`` in
SWE-bench/experiments; trajectories come from the public ``swe-bench-submissions`` S3 bucket named
in the entry's ``metadata.yaml``. Every model call records its USD cost, token usage and timestamp;
every bash result records its return code. Trajectories are converted as they download and the
raw files are deleted.
"""

from __future__ import annotations

import http.client
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Any

from sunkcost.core import StepRecord, datetime_to_us
from sunkcost_bench.common import CACHE, CompactRun, cached_runs, fetch, hash_args, money

COMMIT = "40f164d5b8f1d249bf95a6df8b74b577fd8e519d"
DETAILS_URL = (
    f"https://raw.githubusercontent.com/SWE-bench/experiments/{COMMIT}/evaluation/verified/"
    "{submission}/per_instance_details.json"
)
TRAJ_URL = "https://swe-bench-submissions.s3.amazonaws.com/bash-only/{submission}/trajs/{iid}/{iid}.traj.json"
# The 2026-02-17 mini-SWE-agent 2.0.0 entries that publish per-instance labels. Excluded:
# 20260219_mini-v2.0.0_gpt-5-2-codex (no per_instance_details.json) and
# 20260226_mini-v2.0.0_gemini-3-pro-high (records 0 resolved instances, an incomplete entry).
SUBMISSIONS = (
    "20260217_mini-v2.0.0_claude-4-5-haiku-high",
    "20260217_mini-v2.0.0_claude-4-5-opus-high",
    "20260217_mini-v2.0.0_claude-4-5-sonnet-high",
    "20260217_mini-v2.0.0_claude-4-6-opus",
    "20260217_mini-v2.0.0_deepseek-3-2-high",
    "20260217_mini-v2.0.0_gemini-3-flash-high",
    "20260217_mini-v2.0.0_glm-5-high",
    "20260217_mini-v2.0.0_gpt-5-2-high",
    "20260217_mini-v2.0.0_gpt-5-mini",
    "20260217_mini-v2.0.0_kimi-k2-5-high",
    "20260217_mini-v2.0.0_minimax-2-5-high",
)


def _us(seconds: float | str) -> int:
    return datetime_to_us(datetime.fromtimestamp(float(seconds), tz=timezone.utc))


def _failed(extra: dict[str, Any]) -> bool:
    return str(extra.get("returncode", "0")) != "0" or bool(extra.get("exception_info"))


def convert(traj: dict[str, Any], resolved: bool, submission: str) -> CompactRun | None:
    """Both trajectory formats: chat-completions messages and Responses-API items."""
    steps: list[StepRecord] = []
    costs = []
    calls: dict[str, tuple[str, Any]] = {}
    started: int | None = None
    last_ts: int | None = None
    for m in traj["messages"]:
        extra = m.get("extra") or {}
        if m.get("role") == "assistant" or m.get("object") == "response":
            if m.get("role") == "assistant":
                response = extra.get("response") or {}
                usage = response.get("usage") or {}
                model, tin, tout = (
                    response.get("model"),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                )
                created = response.get("created")
                for call in m.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    calls[call.get("id")] = (fn.get("name") or "bash", fn.get("arguments"))
            else:
                usage = m.get("usage") or {}
                model, tin, tout = (
                    m.get("model"),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
                created = m.get("created_at")
                for item in m.get("output") or []:
                    if item.get("type") == "function_call":
                        calls[item.get("call_id")] = (
                            item.get("name") or "bash",
                            item.get("arguments"),
                        )
            if "timestamp" not in extra:
                return None
            ts = _us(extra["timestamp"])
            if started is None:
                started = _us(created) if created is not None else ts
            cost = money(extra.get("cost"))
            steps.append(
                StepRecord(
                    "model",
                    model,
                    None,
                    None,
                    int(tin or 0),
                    int(tout or 0),
                    float(cost),
                    False,
                    ts,
                )
            )
            costs.append(cost)
            last_ts = ts
        elif m.get("role") == "tool" or m.get("type") == "function_call_output":
            call_id = m.get("tool_call_id") or m.get("call_id")
            name, arguments = calls.get(call_id, ("bash", None))
            ts = _us(extra["timestamp"]) if "timestamp" in extra else (last_ts or 0)
            args = None if arguments is None else hash_args(arguments)
            steps.append(StepRecord("tool", None, name, args, 0, 0, 0.0, _failed(extra), ts))
            costs.append(money(0))
            last_ts = ts
    if started is None or not steps:
        return None
    iid = traj.get("instance_id")
    return CompactRun(
        run_id=f"swe-bench/{submission}/{iid}",
        batch=submission,
        group=f"swe-bench/{iid}",
        started_at_us=min(started, steps[0].ts_us),
        outcome="success" if resolved else "failure",
        steps=tuple(steps),
        costs=tuple(costs),
    )


def _one(submission: str, iid: str, resolved: bool) -> CompactRun | None:
    """Download, convert and delete one trajectory; a download that does not parse is retried.

    ``None`` means the trajectory could not be fetched or has no usable steps; the caller reports
    how many.
    """
    raw = CACHE / "raw" / "swe-bench" / submission / f"{iid}.traj.json"
    url = TRAJ_URL.format(submission=submission, iid=iid)
    for _ in range(3):
        try:
            fetch(url, raw)
            return convert(json.loads(raw.read_text("utf-8")), resolved, submission)
        except ValueError:
            continue  # truncated JSON: fetch again
        except (OSError, http.client.HTTPException):
            return None  # fetch already retried
        finally:
            raw.unlink(missing_ok=True)
    return None


def _submission(submission: str, workers: int) -> list[CompactRun]:
    details_path = fetch(
        DETAILS_URL.format(submission=submission),
        CACHE / "raw" / "swe-bench" / f"{submission}.details.json",
    )
    details = json.loads(details_path.read_text("utf-8"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda kv: _one(submission, kv[0], bool(kv[1].get("resolved"))), details.items()
            )
        )
    runs = [r for r in results if r is not None]
    print(
        f"swe-bench {submission}: {len(runs)} of {len(details)} trajectories converted",
        file=sys.stderr,
    )
    return runs


def load(workers: int = 16) -> list[CompactRun]:
    """Each submission is cached on its own, so an interrupted download resumes where it stopped."""
    runs: list[CompactRun] = []
    for submission in SUBMISSIONS:
        path = CACHE / "runs" / "swe-bench" / f"{submission}.jsonl.gz"
        runs.extend(cached_runs(path, partial(_submission, submission, workers)))
    return runs
