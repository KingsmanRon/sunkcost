"""G2b: 500 simulated workloads; the share whose realised future false-abort rate exceeds α must be
at most δ + 0.03 in every (target_recall, δ) cell. Fixed seeds; run with ``make test-stats``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sunkcost_worker.simulation import run_simulations, summarize

pytestmark = pytest.mark.stats

WORKLOADS = int(os.environ.get("SUNKCOST_G2B_WORKLOADS", "500"))
SEED = 20260923


def test_recall_guarantee_holds_across_workloads(tmp_path: Path) -> None:
    summary = summarize(run_simulations(WORKLOADS, SEED))
    out = Path(os.environ.get("SUNKCOST_G2B_OUT", tmp_path / "g2b.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"\nG2b: {summary['workloads']} workloads, statuses {summary['statuses']}")
    print("target  delta  with_tau  exceeding  share   allowed  mean_FA  p95_FA   max_FA")
    for c in summary["cells"]:  # type: ignore[attr-defined]
        print(
            f"{c['target_recall']:>6}  {c['delta']:>5}  {c['with_threshold']:>8}  "
            f"{c['exceeding_alpha']:>9}  {c['exceed_share']:.4f}  {c['allowed_share']:.2f}     "
            f"{c['future_rate_mean']:.4f}   {c['future_rate_p95']:.4f}   {c['future_rate_max']:.4f}"
        )
    for c in summary["cells"]:  # type: ignore[attr-defined]
        # Not vacuous: most workloads reach threshold selection.
        assert c["with_threshold"] >= 0.8 * c["workloads"], c
        assert c["exceed_share"] <= c["allowed_share"], c
