"""Sunkcost for the OpenAI Agents SDK, via run hooks.

    run = sl.start_run("support.billing_dispute")
    result = await Runner.run(agent, "…", hooks=SunkcostRunHooks(run))

``check()`` runs in ``on_llm_start`` and ``on_tool_start``, which the SDK awaits immediately before
each model call and each local tool call. On ABORT the hook raises ``RunAborted``. Before a model
call it propagates out of ``Runner.run`` unchanged; before a tool call the Agents SDK wraps it in
``UserError("Error running tool …")``. ``run_agent`` re-raises both as a plain ``RunAborted``, and
``find_abort(exc)`` finds it in any wrapped exception. ``on_llm_end`` records the model call with
its usage, and ``on_tool_end`` records the tool call. Tool arguments
(``ToolContext.tool_arguments``) are HMAC-hashed by the SDK and never sent.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from agents import Agent, ModelResponse, RunContextWrapper, RunHooks, Runner, RunResult, Tool

from sunkcost import Decision, Run, RunAborted, find_abort

__all__ = ["RunAborted", "SunkcostRunHooks", "find_abort", "run_agent"]

log = logging.getLogger("sunkcost")


def _model_name(agent: Agent[Any]) -> str | None:
    model = agent.model
    if isinstance(model, str):
        return model
    return getattr(model, "model", None) if model is not None else None


def _tool_args(context: RunContextWrapper[Any]) -> Any:
    raw = getattr(context, "tool_arguments", None)  # set on ToolContext for function tools
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class SunkcostRunHooks(RunHooks[Any]):
    """``raise_on_abort=False`` records decisions without stopping the run (see ``aborted``)."""

    def __init__(self, run: Run, *, raise_on_abort: bool = True) -> None:
        self.run = run
        self.raise_on_abort = raise_on_abort
        self.aborted: Decision | None = None
        self._llm_t0: float | None = None
        self._tool_t0: dict[int, tuple[float, Any]] = {}

    def _before(self) -> None:
        decision = self.run.check()  # never raises
        if decision.abort:
            self.aborted = decision
            if self.raise_on_abort:
                raise RunAborted(decision, self.run.run_id)

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        self._before()
        self._llm_t0 = time.perf_counter()

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse
    ) -> None:
        try:
            latency = (
                None if self._llm_t0 is None else round((time.perf_counter() - self._llm_t0) * 1000)
            )
            self._llm_t0 = None
            usage = response.usage
            self.run.record_step(
                "model",
                model=_model_name(agent),
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                latency_ms=latency,
            )
        except Exception:  # fail-open
            log.debug("sunkcost-openai-agents: on_llm_end failed", exc_info=True)

    async def on_tool_start(self, context: RunContextWrapper[Any], agent: Any, tool: Tool) -> None:
        self._before()
        self._tool_t0[id(context)] = (time.perf_counter(), _tool_args(context))

    async def on_tool_end(
        self, context: RunContextWrapper[Any], agent: Any, tool: Tool, result: object
    ) -> None:
        try:
            t0, args = self._tool_t0.pop(id(context), (None, None))
            latency = None if t0 is None else round((time.perf_counter() - t0) * 1000)
            self.run.record_step(
                "tool",
                tool_name=getattr(tool, "name", None) or "tool",
                tool_args=args,
                latency_ms=latency,
            )
        except Exception:
            log.debug("sunkcost-openai-agents: on_tool_end failed", exc_info=True)


async def run_agent(agent: Agent[Any], input: Any, *, run: Run, **kwargs: Any) -> RunResult:
    """``Runner.run`` with Sunkcost hooks; an abort surfaces as ``RunAborted``, however wrapped."""
    try:
        return await Runner.run(agent, input, hooks=SunkcostRunHooks(run), **kwargs)
    except RunAborted:
        raise
    except Exception as exc:
        aborted = find_abort(exc)
        if aborted is None:
            raise
        raise RunAborted(aborted.decision, aborted.run_id) from exc
