"""Sunkcost for Cycles (``runcycles``): ``check()`` before every Cycles reservation.

Cycles reserves budget before an action and commits the actual cost after it. Sunkcost decides
whether the action should happen at all, so ``check()`` runs first; on ABORT no reservation is
made. Stack ``guard`` above ``@cycles``:

    @guard(kind="model", name="gpt-4o", usage=lambda r: {"input_tokens": r.tin})
    @cycles(estimate=2_000_000, action_kind="llm.completion", action_name="gpt-4o")
    def call_llm(prompt): ...

    with bind_run(sl.start_run("support.billing_dispute")) as run:
        call_llm("…")             # raises RunAborted before Cycles reserves anything

``guard`` must sit outside ``@cycles``: the Cycles lifecycle retries a failed ``create_reservation``
once and re-raises the failure as ``CyclesProtocolError``, so an abort raised from inside it would
not reach the agent as ``RunAborted``. For direct client use, ``reserve`` and ``commit`` wrap
``create_reservation`` and ``commit_reservation``; ``commit`` records the step with the actual
cost when the amount is in ``USD_MICROCENTS``.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal
from typing import Any, TypeVar

from sunkcost import Decision, Run, RunAborted

__all__ = ["RunAborted", "bind_run", "commit", "current_run", "guard", "reserve"]

log = logging.getLogger("sunkcost")
F = TypeVar("F", bound=Callable[..., Any])
MICROCENTS_PER_USD = Decimal(100_000_000)  # 1 USD = 100 cents = 10^8 microcents

_current: contextvars.ContextVar[Run | None] = contextvars.ContextVar("sunkcost_run", default=None)


@contextlib.contextmanager
def bind_run(run: Run) -> Iterator[Run]:
    """Make ``run`` the current run for guarded calls in this context (thread or task)."""
    token = _current.set(run)
    try:
        yield run
    finally:
        _current.reset(token)


def current_run() -> Run | None:
    return _current.get()


def _check(run: Run, raise_on_abort: bool) -> Decision:
    decision = run.check()  # never raises
    if decision.abort and raise_on_abort:
        raise RunAborted(decision, run.run_id)
    return decision


def guard(
    *,
    kind: str = "model",
    name: str | None = None,
    usage: Callable[[Any], Mapping[str, Any]] | None = None,
    raise_on_abort: bool = True,
) -> Callable[[F], F]:
    """Decorator: ``check()`` before the call, ``record_step()`` after it (sync or async).

    ``name`` is the model (``kind="model"``) or tool name. ``usage(result)`` may return
    ``input_tokens``, ``output_tokens``, ``cost_usd`` and ``tool_args`` for the step. With no
    bound run the call passes straight through. With ``raise_on_abort=False`` an aborted call is
    skipped and returns ``None``.
    """

    def record(run: Run, result: Any, t0: float, error: bool) -> None:
        try:
            extra = dict(usage(result)) if (usage is not None and not error) else {}
            names = {"model": name} if kind == "model" else {"tool_name": name or "tool"}
            run.record_step(
                kind,
                **names,
                error=error,
                latency_ms=round((time.perf_counter() - t0) * 1000),
                **extra,
            )
        except Exception:
            log.debug("sunkcost-cycles: record failed", exc_info=True)

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                run = _current.get()
                if run is None:
                    return await fn(*args, **kwargs)
                if _check(run, raise_on_abort).abort:
                    return None
                t0 = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                except BaseException:
                    record(run, None, t0, True)
                    raise
                record(run, result, t0, False)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            run = _current.get()
            if run is None:
                return fn(*args, **kwargs)
            if _check(run, raise_on_abort).abort:
                return None
            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                record(run, None, t0, True)
                raise
            record(run, result, t0, False)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def _field(request: Any, *path: str) -> Any:
    value = request
    for key in path:
        value = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
        if value is None:
            return None
    return value


def reserve(
    client: Any, request: Any, *, run: Run | None = None, raise_on_abort: bool = True
) -> Any:
    """``check()``, then ``client.create_reservation(request)`` unless the decision is ABORT."""
    run = run or _current.get()
    if run is not None and _check(run, raise_on_abort).abort:
        return None
    return client.create_reservation(request)


def commit(
    client: Any,
    reservation_id: str,
    request: Any,
    *,
    kind: str = "model",
    name: str | None = None,
    run: Run | None = None,
    **step: Any,
) -> Any:
    """``client.commit_reservation`` and then ``record_step`` with the committed actual cost."""
    response = client.commit_reservation(reservation_id, request)
    run = run or _current.get()
    if run is not None:
        try:
            unit = _field(request, "actual", "unit")
            amount = _field(request, "actual", "amount")
            unit = getattr(unit, "value", unit)
            if unit == "USD_MICROCENTS" and isinstance(amount, int) and "cost_usd" not in step:
                step["cost_usd"] = Decimal(amount) / MICROCENTS_PER_USD
            names = {"model": name} if kind == "model" else {"tool_name": name or "tool"}
            run.record_step(kind, **names, **step)
        except Exception:
            log.debug("sunkcost-cycles: commit record failed", exc_info=True)
    return response
