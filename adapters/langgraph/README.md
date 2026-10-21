# sunkcost-langgraph

Sunkcost for LangGraph and LangChain: a callback handler that calls `check()` before each model
and tool invocation and records each completed call.

```python
from sunkcost_langgraph import SunkcostCallbackHandler, is_abort

run = sl.start_run("support.refund_lookup")
graph.invoke(state, config={"callbacks": [SunkcostCallbackHandler(run)]})
```

On ABORT the handler raises `RunAborted` inside the node; route it with a node `error_handler`
(`is_abort(error.error)`). With `ToolNode`, use a narrow `handle_tool_errors` type. Example:
[`examples/langgraph_agent.py`](../../examples/langgraph_agent.py).
