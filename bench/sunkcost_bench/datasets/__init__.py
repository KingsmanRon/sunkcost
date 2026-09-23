"""Datasets with per-run success labels; provenance and licences in bench/DATASETS.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sunkcost_bench.common import CompactRun
from sunkcost_bench.datasets import swebench, tau2, taubench

TAU2_SOURCE = f"github.com/sierra-research/tau2-bench @ {tau2.COMMIT[:7]}, data/tau2/results/final"


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    domain: str
    source: str
    licence: str
    licence_confirmed: bool
    cost_source: str
    cost_unit: str
    has_timestamps: bool
    load: Callable[[], list[CompactRun]]


DATASETS: dict[str, Dataset] = {
    d.key: d
    for d in (
        Dataset(
            key="tau2-retail",
            title="τ²-bench retail",
            domain="customer service (retail)",
            source=TAU2_SOURCE,
            licence="MIT",
            licence_confirmed=True,
            cost_source="recorded USD per model call",
            cost_unit="USD",
            has_timestamps=True,
            load=lambda: tau2.load("retail"),
        ),
        Dataset(
            key="tau2-telecom",
            title="τ²-bench telecom",
            domain="customer service (telecom, dual control)",
            source=TAU2_SOURCE,
            licence="MIT",
            licence_confirmed=True,
            cost_source="recorded USD per model call",
            cost_unit="USD",
            has_timestamps=True,
            load=lambda: tau2.load("telecom"),
        ),
        Dataset(
            key="tau2-airline",
            title="τ²-bench airline",
            domain="customer service (airline)",
            source=TAU2_SOURCE,
            licence="MIT",
            licence_confirmed=True,
            cost_source="recorded USD per model call",
            cost_unit="USD",
            has_timestamps=True,
            load=lambda: tau2.load("airline"),
        ),
        Dataset(
            key="tau-bench-retail",
            title="τ-bench retail (historical trajectories)",
            domain="customer service (retail)",
            source=(
                f"github.com/sierra-research/tau-bench @ {taubench.COMMIT[:7]}, "
                "historical_trajectories"
            ),
            licence="MIT",
            licence_confirmed=True,
            cost_source="none recorded: step count is the cost proxy (1 unit per step)",
            cost_unit="steps",
            has_timestamps=False,
            load=lambda: taubench.load("retail"),
        ),
        Dataset(
            key="swe-bench-verified-mini",
            title="SWE-bench Verified, mini-SWE-agent 2.0.0",
            domain="software engineering (bash-only agent)",
            source=(
                f"github.com/SWE-bench/experiments @ {swebench.COMMIT[:7]} (labels) and "
                "s3://swe-bench-submissions/bash-only (trajectories)"
            ),
            licence="none stated (see DATASETS.md); used for evaluation only, not redistributed",
            licence_confirmed=False,
            cost_source="recorded USD per model call",
            cost_unit="USD",
            has_timestamps=True,
            load=swebench.load,
        ),
    )
}
