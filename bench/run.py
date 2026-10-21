"""Run the benchmark: load each dataset, calibrate with the exact worker code, write results.

    uv run python bench/run.py                    # every dataset; exit 0 iff S1 is met
    uv run python bench/run.py --datasets tau2-retail

Writes bench/results/<dataset>.json, bench/results/<dataset>.png (and -dark.png), and
docs/BENCHMARKS.md. Downloads are cached in bench/.cache/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from sunkcost_bench.common import BENCH_ROOT
from sunkcost_bench.datasets import DATASETS
from sunkcost_bench.evaluate import evaluate_dataset
from sunkcost_bench.plot import plot_dataset
from sunkcost_bench.report import render

RESULTS = BENCH_ROOT / "results"
REPORT = BENCH_ROOT.parent / "docs" / "BENCHMARKS.md"


def _commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            [git, "describe", "--always", "--dirty", "--abbrev=7"],  # "-dirty": uncommitted edits
            capture_output=True,
            text=True,
            check=True,
            cwd=BENCH_ROOT,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sunkcost benchmark (gate G1)")
    parser.add_argument(
        "--datasets", default=",".join(DATASETS), help="comma-separated dataset keys"
    )
    args = parser.parse_args(argv)
    keys = [k for k in args.datasets.split(",") if k]
    unknown = sorted(set(keys) - set(DATASETS))
    if unknown:
        parser.error(f"unknown datasets: {', '.join(unknown)}")

    commit = _commit()  # before any output is written, so "-dirty" means edited code
    RESULTS.mkdir(parents=True, exist_ok=True)
    for key in keys:
        dataset = DATASETS[key]
        t0 = time.monotonic()
        runs = dataset.load()
        doc = {"commit": commit, **evaluate_dataset(dataset, runs)}
        (RESULTS / f"{key}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )
        plot_dataset(doc, RESULTS / f"{key}.png", "light")
        plot_dataset(doc, RESULTS / f"{key}-dark.png", "dark")
        verdict = "pass" if doc["s1"]["met"] else "fail"
        print(
            f"{key}: {len(runs)} runs, S1 {verdict} ({doc['s1']['why']}) "
            f"[{time.monotonic() - t0:.0f}s]"
        )

    docs = [
        json.loads(p.read_text("utf-8"))
        for p in sorted(RESULTS.glob("*.json"))
        if p.stem in DATASETS
    ]
    docs.sort(key=lambda d: list(DATASETS).index(d["dataset"]))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    REPORT.write_text(render(docs, generated), "utf-8")
    met = sum(1 for d in docs if d["s1"]["met"])
    print(
        f"S1: met on {met} of {len(docs)} datasets ({'PASS' if met >= 2 else 'FAIL'}); "
        f"wrote {REPORT}"
    )
    return 0 if met >= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
