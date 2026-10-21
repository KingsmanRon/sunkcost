# sunkcost-openai-agents

Sunkcost for the OpenAI Agents SDK: run hooks that call `check()` before each model call and
local tool call and record each completed one.

```python
from sunkcost_openai_agents import run_agent

run = sl.start_run("research.find_citation")
result = await run_agent(agent, "…", run=run)  # raises RunAborted on ABORT
```

`SunkcostRunHooks(run)` can also be passed to `Runner.run(..., hooks=...)` directly; use
`find_abort(exc)` there, because the Agents SDK wraps exceptions from tool hooks. Example:
[`examples/openai_agents_agent.py`](../../examples/openai_agents_agent.py).
