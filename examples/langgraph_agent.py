"""A LangGraph tool-calling agent stopped by Sunkcost; the abort is routed by a node error handler.

    uv run python examples/langgraph_agent.py

Offline: the chat model is LangChain's ``FakeMessagesListChatModel`` and the bundle is signed with
the public RFC 8032 test key, which only this example trusts.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.tools import ToolException, tool
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from sunkcost.testing import demo_client
from sunkcost_langgraph import SunkcostCallbackHandler, is_abort

WORKLOAD = "support.refund_lookup"


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    status: str


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by ID."""
    raise ToolException("order service timed out")


def scripted_turn(i: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "lookup_order", "args": {"order_id": "A-17"}, "id": f"call-{i}"}],
        usage_metadata={
            "input_tokens": 900 + 150 * i,
            "output_tokens": 40,
            "total_tokens": 940 + 150 * i,
        },
    )


model = FakeMessagesListChatModel(responses=[scripted_turn(i) for i in range(20)])


def agent(state: State) -> dict[str, Any]:
    return {"messages": [model.invoke(state["messages"])]}


def on_error(state: State, error: NodeError) -> Command[Any]:
    """The graph's error edge: a Sunkcost abort ends the run cleanly; anything else is re-raised."""
    if is_abort(error.error):
        return Command(update={"status": f"aborted by sunkcost in node {error.node!r}"}, goto=END)
    raise error.error


graph = StateGraph(State)
graph.add_node("agent", agent, error_handler=on_error)
# Handle only tool failures in ToolNode, so a RunAborted raised before a tool call reaches on_error.
tools = ToolNode([lookup_order], handle_tool_errors=ToolException)
graph.add_node("tools", tools, error_handler=on_error)
graph.add_edge(START, "agent")
graph.add_edge("agent", "tools")
graph.add_edge("tools", "agent")
app = graph.compile()


def main() -> str:
    # The score rises as the agent repeats the same failing lookup; ABORT once it reaches 0.8.
    sl = demo_client(
        WORKLOAD, weights={"repeat_action_rate": "6.0"}, intercept="-4.0", threshold="0.8"
    )
    run = sl.start_run(WORKLOAD)
    handler = SunkcostCallbackHandler(run)
    result = app.invoke(
        {"messages": [HumanMessage("Where is my refund for order A-17?")], "status": "running"},
        config={"callbacks": [handler], "recursion_limit": 50},
    )
    run.end("aborted" if handler.aborted else "completed")
    print(f"status: {result['status']}; {run.steps} steps recorded; decision {handler.aborted}")
    sl.close()
    return str(result["status"])


if __name__ == "__main__":
    main()
