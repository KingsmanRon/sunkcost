"""Synthetic workloads with a known failure process.

Used by the calibration unit tests, the G2b statistical simulation, and later the integration and
staging gates. Runs are i.i.d. given the process, so history and future are exchangeable, which
is the assumption the recall guarantee needs.

A failing run behaves like a successful one until an onset step. After it, with probability
``signal``, it degrades: it repeats one tool call, errors more often and stays in a cycle. With
probability ``1 - signal`` it shows nothing unusual and fails anyway, which no policy can catch.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from decimal import Decimal

from sunkcost.core import StepRecord
from sunkcost_worker.calibration.data import Outcome, RunData

_TOOLS = ("search", "fetch", "read_file", "edit_file", "run_tests", "lookup", "summarize", "plan")
_MICROS = Decimal("0.000001")


@dataclass(frozen=True)
class WorkloadProcess:
    p_failure: float = 0.3
    signal: float = 0.8
    mean_length: float = 14.0
    onset_mean: float = 5.0
    loop_extra_steps: float = 8.0
    error_rate_success: float = 0.05
    error_rate_loop: float = 0.4
    price_input_per_mtok: float = 3.0
    price_output_per_mtok: float = 15.0

    @classmethod
    def sample(cls, rng: random.Random) -> WorkloadProcess:
        """A random but learnable process, for simulations over many workloads."""
        return cls(
            p_failure=rng.uniform(0.2, 0.45),
            signal=rng.uniform(0.4, 1.0),
            mean_length=rng.uniform(8.0, 24.0),
            onset_mean=rng.uniform(2.0, 8.0),
            loop_extra_steps=rng.uniform(3.0, 14.0),
            error_rate_success=rng.uniform(0.0, 0.15),
            error_rate_loop=rng.uniform(0.2, 0.7),
            price_input_per_mtok=rng.choice((0.4, 1.0, 3.0, 5.0)),
            price_output_per_mtok=rng.choice((1.6, 5.0, 15.0, 25.0)),
        )


def _hash(label: str) -> str:
    return "hmac-sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _run_id(rng: random.Random, ms: int) -> str:
    rand = rng.getrandbits(74)
    value = (
        (ms << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    )
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def generate_run(
    rng: random.Random,
    process: WorkloadProcess,
    started_at_us: int,
    outcome: Outcome | None = None,
) -> RunData:
    """One run. The outcome is drawn from the process unless given."""
    if outcome is None:
        outcome = "failure" if rng.random() < process.p_failure else "success"
    failing = outcome == "failure"
    length = max(2, int(rng.expovariate(1.0 / process.mean_length)) + 2)
    degrading = failing and rng.random() < process.signal
    onset = max(1, int(rng.expovariate(1.0 / process.onset_mean)))
    if degrading:
        length = max(length, onset + 1) + int(rng.expovariate(1.0 / process.loop_extra_steps)) + 2
    loop_tool = rng.choice(_TOOLS)
    loop_args = _hash(f"loop-{rng.randrange(3)}")

    steps: list[StepRecord] = []
    costs: list[Decimal] = []
    t = started_at_us
    context = rng.randint(600, 2400)
    for i in range(length):
        in_loop = degrading and i >= onset
        if i % 2 == 0:  # model call
            context += rng.randint(80, 900)
            out_tokens = rng.randint(20, 600)
            cost = Decimal(
                (
                    context * process.price_input_per_mtok
                    + out_tokens * process.price_output_per_mtok
                )
                / 1e6
            ).quantize(_MICROS)
            t += rng.randint(300_000, 4_000_000)
            steps.append(
                StepRecord(
                    "model",
                    "synthetic-model",
                    None,
                    None,
                    context,
                    out_tokens,
                    float(cost),
                    False,
                    t,
                )
            )
            costs.append(cost)
        else:
            if in_loop and rng.random() < 0.85:
                tool, args = loop_tool, loop_args
                error = rng.random() < process.error_rate_loop
            else:
                tool = rng.choice(_TOOLS)
                args = _hash(f"{tool}-{rng.randrange(40)}")
                error = rng.random() < process.error_rate_success
            t += rng.randint(20_000, 1_500_000)
            steps.append(StepRecord("tool", None, tool, args, 0, 0, 0.0, error, t))
            costs.append(Decimal(0))
    return RunData(
        run_id=_run_id(rng, started_at_us // 1000),
        started_at_us=started_at_us,
        steps=tuple(steps),
        costs=tuple(costs),
        outcome=outcome,
    )


def generate_history(
    rng: random.Random, process: WorkloadProcess, n_runs: int, start_us: int = 1_790_000_000_000_000
) -> list[RunData]:
    """``n_runs`` runs arriving one every 30 seconds."""
    return [generate_run(rng, process, start_us + i * 30_000_000) for i in range(n_runs)]
