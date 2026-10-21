"""A hand-written agent loop with Sunkcost: check() before each step, record_step() after.

    uv run python examples/basic_loop.py

Runs offline with a fake LLM and no API key. The bundle is signed with the public RFC 8032
*test* key, which only this example trusts. In production, bundles come from the Sunkcost API
and are verified against the production key.
"""

from __future__ import annotations

from dataclasses import dataclass

from sunkcost.testing import demo_client

WORKLOAD = "ci.fix_failing_test"


@dataclass
class Proposal:
    tool: str
    args: dict[str, str]
    input_tokens: int


def fake_llm(turn: int) -> Proposal:
    """Always proposes the same failing command: the kind of loop Sunkcost is meant to stop."""
    return Proposal("run_tests", {"path": "tests/test_api.py"}, 1200 + 300 * turn)


def run_tests(args: dict[str, str]) -> bool:
    return False  # the fix never works


def main() -> str:
    # The score rises as the agent keeps repeating the same actions; ABORT once it reaches 0.8.
    sl = demo_client(
        WORKLOAD, weights={"repeat_action_rate": "6.0"}, intercept="-4.0", threshold="0.8"
    )
    with sl.start_run(WORKLOAD, external_ref="CI-1234") as run:
        for turn in range(50):  # a step cap, as any real agent has
            decision = run.check()
            print(
                f"step {run.steps + 1}: {decision.action:8} {decision.reason:16} "
                f"score={decision.score}"
            )
            if decision.abort:
                run.end("aborted")
                break
            proposal = fake_llm(turn)
            run.record_step(
                "model",
                model="claude-haiku-4-5",
                input_tokens=proposal.input_tokens,
                output_tokens=60,
            )
            decision = run.check()
            if decision.abort:
                print(f"step {run.steps + 1}: ABORT    {decision.reason}")
                run.end("aborted")
                break
            ok = run_tests(proposal.args)
            run.record_step("tool", tool_name=proposal.tool, tool_args=proposal.args, error=not ok)
    print(f"stopped after {run.steps} steps, spent ${run.total_cost_usd}")
    sl.close()
    return decision.reason


if __name__ == "__main__":
    main()
