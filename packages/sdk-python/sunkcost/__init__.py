"""Sunkcost Python SDK: stop agent runs that are going to fail, with a recall guarantee.

``check()`` decides locally against a signed policy bundle and never makes a network call. Events
are buffered and sent in the background. Every call is fail-open: an SDK problem never stops,
slows or errors the agent (§10).
"""

__version__ = "0.1.0"

from sunkcost.client import Run, Stats, Sunkcost
from sunkcost.decision import Decision
from sunkcost.errors import RunAborted, find_abort

__all__ = ["Decision", "Run", "RunAborted", "Stats", "Sunkcost", "__version__", "find_abort"]
