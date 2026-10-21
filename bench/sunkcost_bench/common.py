"""Shared plumbing: the compact run format, downloads, arrival order and conversion helpers.

Loaders turn each public trajectory into a *compact run*: the same fields the SDK would send on the
wire (step kind, model, tool name, HMAC of the arguments, token counts, cost, error flag,
timestamp) and nothing else. No prompt, completion, argument or tool-output text is kept.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import http.client
import json
import random
import time
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from sunkcost.core import StepRecord, args_hash
from sunkcost_worker.calibration import RunData

BENCH_ROOT = Path(__file__).resolve().parent.parent
CACHE = BENCH_ROOT / ".cache"
# Arguments are hashed as the SDK would hash them. The salt only has to be fixed; it is not secret.
ARGS_SALT = "sunkcost-bench-v1"
# Synthetic epoch for re-timed arrival orders (2026-01-01T00:00:00Z).
ARRIVAL_EPOCH_US = 1_767_225_600_000_000
_NINE_PLACES = Decimal("0.000000001")


@dataclass(frozen=True)
class CompactRun:
    run_id: str
    batch: str
    group: str
    started_at_us: int
    outcome: str
    steps: tuple[StepRecord, ...]
    costs: tuple[Decimal, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "batch": self.batch,
            "group": self.group,
            "started_at_us": self.started_at_us,
            "outcome": self.outcome,
            "steps": [
                [
                    s.kind,
                    s.model,
                    s.tool_name,
                    s.args_hash,
                    s.input_tokens,
                    s.output_tokens,
                    format(c, "f"),
                    s.error,
                    s.ts_us,
                ]
                for s, c in zip(self.steps, self.costs, strict=True)
            ],
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> CompactRun:
        steps, costs = [], []
        for kind, model, tool, h, tin, tout, cost, error, ts in doc["steps"]:
            steps.append(StepRecord(kind, model, tool, h, tin, tout, float(cost), error, ts))
            costs.append(Decimal(cost))
        return cls(
            doc["run_id"],
            doc["batch"],
            doc["group"],
            doc["started_at_us"],
            doc["outcome"],
            tuple(steps),
            tuple(costs),
        )

    def to_run_data(self) -> RunData:
        return RunData(
            run_id=self.run_id,
            started_at_us=self.started_at_us,
            steps=self.steps,
            costs=self.costs,
            outcome="success" if self.outcome == "success" else "failure",
            group=self.group,
        )


def money(value: float | int | str | None) -> Decimal:
    """A recorded USD amount as a 9-place decimal (numeric(20,9)); unknown counts as 0."""
    if value is None:
        return Decimal(0)
    return Decimal(repr(value) if isinstance(value, float) else str(value)).quantize(_NINE_PLACES)


def hash_args(arguments: Any) -> str:
    """HMAC of the tool arguments as the SDK computes it; JSON strings are decoded first."""
    if isinstance(arguments, str):
        with contextlib.suppress(ValueError):
            arguments = json.loads(arguments)
    return args_hash(ARGS_SALT, arguments)


def git_blob_sha1(data: bytes) -> str:
    """The object id git gives a file's contents, to check downloads from a pinned commit."""
    # Git object ids are SHA-1 by definition: this matches upstream blobs and signs nothing, so the
    # weak-hash finding does not apply (security/waivers.md).
    blob = b"blob %d\0" % len(data) + data
    return hashlib.sha1(blob, usedforsecurity=False).hexdigest()  # nosemgrep


def fetch(url: str, dest: Path, *, blob_sha1: str | None = None, attempts: int = 4) -> Path:
    """Download ``url`` to ``dest`` unless a verified copy is already there.

    With ``blob_sha1`` the file must match that git blob id; a truncated or altered download is
    deleted and retried.
    """
    if not url.startswith("https://"):
        raise ValueError(f"only https downloads are allowed: {url}")
    if dest.exists() and (blob_sha1 is None or git_blob_sha1(dest.read_bytes()) == blob_sha1):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(attempts):
        try:
            # https only (checked above), from the fixed URLs in bench/DATASETS.md.
            with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310  # nosemgrep
                data = resp.read()
            if blob_sha1 is not None and git_blob_sha1(data) != blob_sha1:
                raise OSError(f"{url}: content does not match git blob {blob_sha1}")
            tmp.write_bytes(data)
            tmp.rename(dest)
            return dest
        except (OSError, http.client.HTTPException):  # HTTPException covers a truncated body
            if attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def write_runs(path: Path, runs: Iterable[CompactRun]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for run in runs:
            fh.write(json.dumps(run.to_json(), separators=(",", ":")) + "\n")


def read_runs(path: Path) -> list[CompactRun]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [CompactRun.from_json(json.loads(line)) for line in fh]


def cached_runs(path: Path, build: Callable[[], list[CompactRun]]) -> list[CompactRun]:
    if not path.exists():
        write_runs(path, build())
    return read_runs(path)


def shift(run: CompactRun, started_at_us: int) -> CompactRun:
    """Move a run in time, keeping every step's offset from the run start."""
    delta = started_at_us - run.started_at_us
    steps = tuple(replace(s, ts_us=s.ts_us + delta) for s in run.steps)
    return replace(run, started_at_us=started_at_us, steps=steps)


def batch_normalized_order(
    runs: Sequence[CompactRun], *, shuffle_seed: str | None = None
) -> list[CompactRun]:
    """Interleave batches by their position within their own batch.

    Every public dataset here was produced one batch at a time (one model, one domain), so raw
    start times would place whole models in the test split. Within each batch, runs are ranked
    by recorded start time; a run's arrival position is ``(rank + 0.5) / batch size``; runs are
    re-timed onto one timeline at those positions. Each batch keeps its own chronology, and every
    split gets its fair share of every batch.

    With ``shuffle_seed``, each batch is put in a seeded random order before ranking instead, so
    every split is a random sample of the batch (the post-hoc sensitivity protocol, ADR-0003).
    """
    by_batch: dict[str, list[CompactRun]] = defaultdict(list)
    for run in runs:
        by_batch[run.batch].append(run)
    out: list[CompactRun] = []
    for batch, batch_runs in by_batch.items():
        batch_runs.sort(key=lambda r: (r.started_at_us, r.run_id))
        if shuffle_seed is not None:
            random.Random(f"{shuffle_seed}/{batch}").shuffle(batch_runs)  # noqa: S311 - not crypto
        n = len(batch_runs)
        for rank, run in enumerate(batch_runs):
            position_us = int((rank + 0.5) / n * 1e12)  # a ~11.6-day synthetic timeline
            out.append(shift(run, ARRIVAL_EPOCH_US + position_us))
    return out
