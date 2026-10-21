"""Exceptions raised to agent code only by adapters configured with ``raise_on_abort=True``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunkcost.decision import Decision


class RunAborted(Exception):  # noqa: N818 - name fixed by ARCHITECTURE.md §4 C4
    """The run's policy returned ABORT before the next step. ``decision`` says why."""

    def __init__(self, decision: Decision, run_id: str) -> None:
        super().__init__(
            f"sunkcost aborted run {run_id}: "
            f"score {decision.score!r} >= threshold {decision.threshold!r}"
        )
        self.decision = decision
        self.run_id = run_id


def find_abort(error: BaseException | None) -> RunAborted | None:
    """The ``RunAborted`` that ``error`` is or wraps (via ``__cause__``/``__context__``), if any.

    Frameworks often re-raise hook exceptions wrapped in their own error types.
    """
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, RunAborted):
            return error
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return None
