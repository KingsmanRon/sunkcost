"""An OpenAI Agents SDK agent stopped by Sunkcost through run hooks.

    uv run python examples/openai_agents_agent.py

Offline: the model is the Agents SDK's ``ScriptedModel`` (no API key, no network), and the bundle
is signed with the public RFC 8032 test key, which only this example trusts.
"""

from __future__ import annotations

import asyncio

from agents import Agent, function_tool, set_tracing_disabled
from agents.testing import ScriptedModel, function_call
from agents.usage import Usage

from sunkcost import RunAborted
from sunkcost.testing import demo_client
from sunkcost_openai_agents import run_agent

WORKLOAD = "research.find_citation"


@function_tool
def search(query: str) -> str:
    """Search the paper index."""
    return "no results"


def main() -> str:
    set_tracing_disabled(True)
    model = ScriptedModel(
        [
            [function_call("search", {"query": "sunk cost fallacy"}, call_id=f"c{i}")]
            for i in range(20)
        ],
        default_usage=Usage(requests=1, input_tokens=1500, output_tokens=30, total_tokens=1530),
    )
    agent = Agent(name="researcher", instructions="Find a citation.", model=model, tools=[search])
    # The same call over and over drives repeat_action_rate up; ABORT at score >= 0.8.
    sl = demo_client(
        WORKLOAD, weights={"repeat_action_rate": "6.0"}, intercept="-4.0", threshold="0.8"
    )
    run = sl.start_run(WORKLOAD)
    try:
        asyncio.run(run_agent(agent, "Find a citation.", run=run, max_turns=50))
        run.end("completed")
        status = "completed"
    except RunAborted as aborted:
        run.end("aborted")
        status = f"aborted: {aborted.decision.reason} at step {run.steps + 1}"
    print(f"{status}; {run.steps} steps recorded, {len(model.calls)} model calls made")
    sl.close()
    return status


if __name__ == "__main__":
    main()
