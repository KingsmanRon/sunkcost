"""Sunkcost for LangGraph (and any LangChain runnable), via a LangChain callback handler.

    run = sl.start_run("support.billing_dispute")
    graph.invoke(state, config={"callbacks": [SunkcostCallbackHandler(run)]})

``check()`` runs in ``on_chat_model_start`` (or ``on_llm_start``) and ``on_tool_start``, before
each model and tool invocation. On ABORT the handler raises ``RunAborted`` from that callback, so
the node raises it. Route it with a LangGraph node ``error_handler`` (LangGraph ≥ 1.2), or catch it
around ``invoke``. Completed calls are recorded in ``on_llm_end``/``on_tool_end`` (and the
``*_error`` callbacks, with ``error=True``) with token usage, latency and the tool arguments, which
are HMAC-hashed by the SDK and never sent.

LangChain swallows exceptions raised by callback handlers unless ``raise_error`` is set, so this
handler sets it. It only ever raises ``RunAborted``; every other problem is fail-open.

With ``ToolNode``, pass ``handle_tool_errors=ToolException`` (or another narrow type) rather than
``True``: ``True`` turns the ``RunAborted`` raised before a tool call into a tool message, and the
abort then only takes effect at the next model call.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult

from sunkcost import Decision, Run, RunAborted, find_abort

__all__ = ["RunAborted", "SunkcostCallbackHandler", "is_abort"]

log = logging.getLogger("sunkcost")


def is_abort(error: BaseException | None) -> bool:
    """True if ``error`` (for example ``NodeError.error``) is, or wraps, a Sunkcost abort."""
    return find_abort(error) is not None


def _usage(response: LLMResult) -> tuple[int, int, str | None]:
    tin = tout = 0
    model: str | None = None
    for generations in response.generations:
        for gen in generations:
            if isinstance(gen, ChatGeneration):
                usage = getattr(gen.message, "usage_metadata", None) or {}
                tin += int(usage.get("input_tokens") or 0)
                tout += int(usage.get("output_tokens") or 0)
                meta = getattr(gen.message, "response_metadata", None) or {}
                model = model or meta.get("model_name") or meta.get("model")
    llm_output = response.llm_output or {}
    if not (tin or tout):
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        tin = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
        tout = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)
    model = model or llm_output.get("model_name") or llm_output.get("model")
    return tin, tout, model


class SunkcostCallbackHandler(BaseCallbackHandler):
    """Maps LangChain model and tool callbacks onto one Sunkcost run.

    ``raise_on_abort=False`` records decisions without interrupting the graph; the latest ABORT
    is then available as ``aborted``.
    """

    raise_error = True  # LangChain otherwise logs and swallows RunAborted
    run_inline = True  # keep callbacks on the invoking thread, in order

    def __init__(self, run: Run, *, raise_on_abort: bool = True) -> None:
        super().__init__()
        self.run = run
        self.raise_on_abort = raise_on_abort
        self.aborted: Decision | None = None
        self._started: dict[UUID, tuple[float, str | None, str | None, Any]] = {}

    # -- before each call: check() ----------------------------------------------------------

    def _before(self) -> None:
        decision = self.run.check()  # never raises
        if decision.abort:
            self.aborted = decision
            if self.raise_on_abort:
                raise RunAborted(decision, self.run.run_id)

    def _model_name(
        self,
        serialized: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> str | None:
        params = kwargs.get("invocation_params") or {}
        return (
            (metadata or {}).get("ls_model_name")
            or params.get("model")
            or params.get("model_name")
            or ((serialized or {}).get("kwargs") or {}).get("model")
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._before()
        self._started[run_id] = (
            time.perf_counter(),
            self._model_name(serialized, metadata, kwargs),
            None,
            None,
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._before()
        self._started[run_id] = (
            time.perf_counter(),
            self._model_name(serialized, metadata, kwargs),
            None,
            None,
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._before()
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._started[run_id] = (
            time.perf_counter(),
            None,
            name,
            inputs if inputs is not None else input_str,
        )

    # -- after each call: record_step() -----------------------------------------------------

    def _finish(self, run_id: UUID) -> tuple[int | None, str | None, str | None, Any]:
        started = self._started.pop(run_id, None)
        if started is None:
            return None, None, None, None
        t0, model, tool, args = started
        return round((time.perf_counter() - t0) * 1000), model, tool, args

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            latency, model, _, _ = self._finish(run_id)
            tin, tout, reported = _usage(response)
            self.run.record_step(
                "model",
                model=reported or model,
                input_tokens=tin,
                output_tokens=tout,
                latency_ms=latency,
            )
        except Exception:  # fail-open: never break the graph from the "after" side
            log.debug("sunkcost-langgraph: on_llm_end failed", exc_info=True)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id not in self._started:
            return  # the call never started (for example, it was the one we aborted)
        latency, model, _, _ = self._finish(run_id)
        self.run.record_step("model", model=model, error=True, latency_ms=latency)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            latency, _, tool, args = self._finish(run_id)
            if tool is not None:
                self.run.record_step("tool", tool_name=tool, tool_args=args, latency_ms=latency)
        except Exception:
            log.debug("sunkcost-langgraph: on_tool_end failed", exc_info=True)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id not in self._started:
            return
        latency, _, tool, args = self._finish(run_id)
        self.run.record_step("tool", tool_name=tool, tool_args=args, error=True, latency_ms=latency)
