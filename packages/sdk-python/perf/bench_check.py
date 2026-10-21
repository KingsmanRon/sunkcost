"""G3 (Python half): ``check()`` latency at step 200 must be < 0.5 ms at p99, with no network.

    uv run python packages/sdk-python/perf/bench_check.py [--out docs/PERF.md]

Measures one client with a signed enforce-mode bundle that uses all 12 features, on a run that
has already recorded 200 steps (a model/tool mix with distinct and repeated tool arguments).
Every ``socket.connect`` is intercepted while timing, so a network call fails the gate.
Exit status 0 means the gate passed. Results are written as the Python section of docs/PERF.md.
"""

from __future__ import annotations

import argparse
import gc
import platform
import re
import socket
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sunkcost import Sunkcost, __version__
from sunkcost.core import FEATURE_NAMES
from sunkcost.testing import TEST_TRUSTED_KEYS, make_bundle, sign

ROOT = Path(__file__).resolve().parents[3]
P99_LIMIT_MS = 0.5
PREFIX_STEPS = 200
SAMPLES = 50_000
WARMUP = 5_000
WORKLOAD = "perf.check"
SECTION_START = "<!-- python:start -->"
SECTION_END = "<!-- python:end -->"


def _percentile(sorted_ns: list[int], q: float) -> float:
    i = min(len(sorted_ns) - 1, max(0, round(q * (len(sorted_ns) - 1))))
    return sorted_ns[i] / 1e6


def measure() -> dict[str, Any]:
    client = Sunkcost(
        trusted_keys=TEST_TRUSTED_KEYS, args_hash_salt="perf"
    )  # offline: no transport
    weights = {name: f"{0.05 * (i + 1):.2f}" for i, name in enumerate(FEATURE_NAMES)}
    # A threshold the score never reaches, so every check runs the full path to below_threshold.
    bundle = make_bundle(
        WORKLOAD, issued_at_us=time.time_ns() // 1000, weights=weights, threshold="1.0", min_steps=3
    )
    client.bundles.install(WORKLOAD, sign(bundle))
    run = client.start_run(WORKLOAD)
    if run.bundle is None:
        raise RuntimeError("bundle not installed")

    record_ns: list[int] = []
    for i in range(PREFIX_STEPS):
        t0 = time.perf_counter_ns()
        if i % 3 == 0:
            run.record_step(
                "model", model="claude-sonnet-4-5", input_tokens=1200 + 40 * i, output_tokens=180
            )
        else:
            run.record_step(
                "tool",
                tool_name=f"tool_{i % 7}",
                tool_args={"q": i % 11, "x": 1.5},
                error=i % 9 == 0,
            )
        record_ns.append(time.perf_counter_ns() - t0)
    if run.steps != PREFIX_STEPS:
        raise RuntimeError(f"recorded {run.steps} steps")

    connects: list[object] = []
    original = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> None:
        connects.append(address)
        raise OSError("network call during check()")

    socket.socket.connect = guarded  # type: ignore[method-assign,assignment]
    try:
        for _ in range(WARMUP):
            run.check()
        samples = [0] * SAMPLES
        for i in range(SAMPLES):
            t0 = time.perf_counter_ns()
            d = run.check()
            samples[i] = time.perf_counter_ns() - t0
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]
    if d.reason != "below_threshold":
        raise RuntimeError(f"unexpected decision {d}")
    client.close(0.1)
    samples.sort()
    record_ns.sort()
    return {
        "p50": _percentile(samples, 0.50),
        "p90": _percentile(samples, 0.90),
        "p99": _percentile(samples, 0.99),
        "p999": _percentile(samples, 0.999),
        "max": samples[-1] / 1e6,
        "mean": statistics.fmean(samples) / 1e6,
        "record_p99": _percentile(record_ns, 0.99),
        "connects": len(connects),
        "sdk_errors": client.stats.sdk_errors,
    }


def render(r: dict[str, Any], passed: bool) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cpu = platform.processor() or platform.machine()
    return f"""{SECTION_START}
## Python SDK (`sunkcost` {__version__})

Measured on {when} with `make bench-sdk` (`packages/sdk-python/perf/bench_check.py`):
CPython {platform.python_version()} on {platform.system()} {platform.release()} ({cpu}),
garbage collector enabled. One run with {PREFIX_STEPS} recorded steps, an enforce-mode bundle using
all 12 features, {SAMPLES:,} timed `check()` calls after {WARMUP:,} warm-up calls.

| Metric | Value |
|---|---|
| `check()` p50 | {r["p50"]:.4f} ms |
| `check()` p90 | {r["p90"]:.4f} ms |
| `check()` p99 | **{r["p99"]:.4f} ms** (limit {P99_LIMIT_MS} ms) |
| `check()` p99.9 | {r["p999"]:.4f} ms |
| `check()` max | {r["max"]:.4f} ms |
| `record_step()` p99 over the 200 recorded steps | {r["record_p99"]:.4f} ms |
| Network connects attempted during `check()` | {r["connects"]} |

**G3 (Python): {"PASS" if passed else "FAIL"}.**
{SECTION_END}"""


def write(out: Path, section: str) -> None:
    header = (
        "# Performance\n\nMeasured, not claimed (ARCHITECTURE.md §13 G3, S2). Each section is "
        "regenerated by `make bench-sdk`; do not edit by hand.\n\n"
    )
    text = out.read_text("utf-8") if out.exists() else header
    pattern = re.compile(re.escape(SECTION_START) + ".*?" + re.escape(SECTION_END), re.DOTALL)
    text = (
        pattern.sub(lambda _: section, text)
        if pattern.search(text)
        else text.rstrip() + "\n\n" + section
    )
    out.write_text(text.rstrip() + "\n", "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "PERF.md")
    args = parser.parse_args()
    gc.collect()
    result = measure()
    passed = result["p99"] < P99_LIMIT_MS and result["connects"] == 0 and result["sdk_errors"] == 0
    write(args.out, render(result, passed))
    print(
        f"check() at step {PREFIX_STEPS}: p50 {result['p50']:.4f} ms, p99 {result['p99']:.4f} ms, "
        f"max {result['max']:.4f} ms; connects {result['connects']}; "
        f"G3 Python {'PASS' if passed else 'FAIL'}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
