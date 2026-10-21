"""The public client: ``Sunkcost`` and ``Run`` (§4 C2/C3).

Every public method is fail-open. An internal exception is counted in ``stats.sdk_errors`` and
``check()`` then returns ``CONTINUE`` with reason ``sdk_error``; nothing raises into agent code.
``check()`` reads only in-memory state: the feature state of the run and the bundle manager's
current bundle and override. It never performs I/O.
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import secrets
import threading
import time
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sunkcost._ids import is_uuid7, uuid7
from sunkcost._money import ZERO, money_str, to_money
from sunkcost.buffer import EventBuffer
from sunkcost.bundle import Bundle, load_trusted_keys
from sunkcost.bundles import BundleManager
from sunkcost.core import (
    CanonicalJSONError,
    FeatureState,
    StepRecord,
    args_hash,
    format_timestamp_us,
    is_holdout,
)
from sunkcost.decision import NO_BUNDLE, SDK_ERROR, Decision, decide, effective_mode
from sunkcost.errors import RunAborted
from sunkcost.keys import DEFAULT_TRUSTED_KEYS
from sunkcost.prices import PriceTable
from sunkcost.transport import HttpTransport, Transport

log = logging.getLogger("sunkcost")

WORKLOAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
STEP_KINDS = ("model", "tool")
RUN_STATUSES = ("completed", "aborted", "errored")
OUTCOMES = ("success", "failure", "abandoned")
MAX_TEXT = 256
MAX_EXTERNAL_REF = 128
MAX_TOKENS = 1_000_000_000
MAX_LATENCY_MS = 86_400_000
MAX_STEPS = 1_000_000


def _now_us() -> int:
    return time.time_ns() // 1000


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    """Wire-safe identifier text: str, no NUL, 1..limit characters; anything else is null."""
    if value is None:
        return None
    s = str(value).replace("\x00", "")
    return s[:limit] or None


def _count(value: object, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), high)


@dataclass
class Stats:
    sdk_errors: int = 0
    runs_started: int = 0
    checks: int = 0
    aborts: int = 0
    events_discarded_offline: int = 0


class Sunkcost:
    """One client per process. Thread-safe; each ``Run`` is meant for one agent at a time."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        trusted_keys: Mapping[str, str] | None = None,
        args_hash_salt: str | None = None,
        *,
        workloads: Iterable[str] = (),
        cache_dir: str | os.PathLike[str] | None = None,
        buffer_size: int = 10_000,
        flush_interval_s: float = 2.0,
        flush_batch_size: int = 500,
        bundle_refresh_s: float = 300.0,
        close_timeout_s: float = 3.0,
        transport: Transport | None = None,
        prices: PriceTable | None = None,
    ) -> None:
        self.stats = Stats()
        self._close_timeout_s = close_timeout_s
        self._closed = False
        api_key = api_key or os.environ.get("SUNKCOST_API_KEY")
        endpoint = endpoint or os.environ.get("SUNKCOST_ENDPOINT")
        salt = args_hash_salt or os.environ.get("SUNKCOST_ARGS_SALT")
        if not salt:
            # Hashes stay comparable within this process, which is all the features need.
            log.warning(
                "sunkcost: no args_hash_salt (SUNKCOST_ARGS_SALT); using a random per-process salt"
            )
            salt = secrets.token_hex(32)
        self._salt = salt
        cache = cache_dir or os.environ.get("SUNKCOST_CACHE_DIR") or None
        keys = load_trusted_keys(DEFAULT_TRUSTED_KEYS if trusted_keys is None else trusted_keys)
        if transport is None and api_key and endpoint:
            transport = HttpTransport(endpoint, api_key)
        elif transport is None:
            log.warning(
                "sunkcost: no api_key/endpoint; running offline (decisions only, events discarded)"
            )
        self._transport = transport
        self._prices = prices if prices is not None else PriceTable.bundled()
        self.bundles = BundleManager(transport, keys, refresh_s=bundle_refresh_s, cache_dir=cache)
        self._buffer: EventBuffer | None = None
        if transport is not None:
            self._buffer = EventBuffer(
                transport,
                capacity=buffer_size,
                batch_size=flush_batch_size,
                interval_s=flush_interval_s,
            )
            self._buffer.start()
        for workload in workloads:
            self.bundles.register(workload)
        self.bundles.start()
        _register_exit(self)

    # -- events -----------------------------------------------------------------------------

    def _emit(self, event: dict[str, Any]) -> None:
        if self._buffer is None or self._closed:
            self.stats.events_discarded_offline += 1
            return
        self._buffer.put(event)

    # -- public API -------------------------------------------------------------------------

    def start_run(
        self, workload: str, run_id: str | None = None, external_ref: str | None = None
    ) -> Run:
        try:
            return Run(self, workload, run_id, external_ref)
        except Exception:
            self.stats.sdk_errors += 1
            log.debug("sunkcost: start_run failed", exc_info=True)
            return Run.degraded(self)

    def outcome(self, run_id: str, outcome: str, source: str = "sdk") -> None:
        """Report a run's outcome after the fact (the API's ``/v1/outcomes`` also accepts it)."""
        try:
            if not is_uuid7(run_id) or outcome not in OUTCOMES or SOURCE.fullmatch(source) is None:
                raise ValueError(f"invalid outcome report for run {run_id!r}")
            self._emit(
                {
                    "type": "outcome",
                    "run_id": run_id,
                    "outcome": outcome,
                    "source": source,
                    "reported_at": format_timestamp_us(_now_us()),
                }
            )
        except Exception:
            self.stats.sdk_errors += 1
            log.warning("sunkcost: outcome not recorded", exc_info=True)

    def refresh_bundles(self) -> None:
        """Fetch every registered workload's bundle now (blocking; for scripts and tests)."""
        for workload in list(self.bundles._entries):
            self.bundles.refresh(workload)

    def flush(self, timeout_s: float = 3.0) -> bool:
        return True if self._buffer is None else self._buffer.flush(timeout_s)

    @property
    def dropped_events(self) -> int:
        return 0 if self._buffer is None else self._buffer.dropped_total

    def close(self, timeout_s: float | None = None) -> bool:
        """Flush (bounded, default 3 s) and stop background threads. Safe to call twice."""
        if self._closed:
            return True
        timeout = self._close_timeout_s if timeout_s is None else timeout_s
        ok = True
        try:
            self.bundles.stop()
            if self._buffer is not None:
                ok = self._buffer.close(timeout)
        except Exception:
            self.stats.sdk_errors += 1
            ok = False
        self._closed = True
        return ok

    def __enter__(self) -> Sunkcost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Run:
    """One agent run. Call ``check()`` before each step and ``record_step()`` after it."""

    def __init__(
        self, client: Sunkcost, workload: str, run_id: str | None, external_ref: str | None
    ) -> None:
        self._client = client
        self._degraded = False
        if not isinstance(workload, str) or WORKLOAD.fullmatch(workload) is None:
            raise ValueError(f"invalid workload name {workload!r}")
        self.workload = workload
        if run_id is not None:
            run_id = str(run_id).lower()
            if not is_uuid7(run_id):
                log.warning("sunkcost: run_id %r is not a UUIDv7; generating one", run_id)
                client.stats.sdk_errors += 1
                run_id = None
        self.run_id = run_id or uuid7()
        self.external_ref = _text(external_ref, MAX_EXTERNAL_REF)
        self.started_at_us = _now_us()
        client.bundles.register(workload)
        # A run is evaluated against the bundle it started with (ADR-0002 §8).
        bundle, override = client.bundles.get(workload)
        self.bundle: Bundle | None = bundle
        self.holdout = bundle is not None and is_holdout(self.run_id, bundle.holdout_rate)
        self._state = FeatureState(self.started_at_us)
        self._lock = threading.Lock()
        self._last_decision: Decision | None = None
        self._costs: list[Decimal] = []
        self._total = ZERO
        self._would_abort_step: int | None = None
        self._cost_through_would_abort: Decimal | None = None
        self._aborted_step: int | None = None
        self._ended = False
        client.stats.runs_started += 1
        client._emit(
            {
                "type": "run_start",
                "run_id": self.run_id,
                "workload": workload,
                "external_ref": self.external_ref,
                "started_at": format_timestamp_us(self.started_at_us),
                "bundle_id": None if bundle is None else bundle.bundle_id,
                "bundle_version": None if bundle is None else bundle.version,
                "mode": effective_mode(bundle, override, self.started_at_us),
                "holdout": self.holdout,
            }
        )

    @classmethod
    def degraded(cls, client: Sunkcost) -> Run:
        """A run that only ever answers CONTINUE/sdk_error and emits nothing."""
        run = cls.__new__(cls)
        run._client, run._degraded = client, True
        run.workload, run.run_id, run.external_ref = "", uuid7(), None
        run.started_at_us, run.bundle, run.holdout = _now_us(), None, False
        run._ended = False
        return run

    @property
    def steps(self) -> int:
        return 0 if self._degraded else self._state.k

    @property
    def total_cost_usd(self) -> Decimal:
        return ZERO if self._degraded else self._total

    # -- hot path ---------------------------------------------------------------------------

    def check(self) -> Decision:
        """Decide whether to run the next step. No I/O; never raises."""
        if self._degraded:
            return SDK_ERROR
        client = self._client
        try:
            if self._ended:
                return self._last_decision or NO_BUNDLE
            override = client.bundles.override(self.workload)
            with self._lock:
                d = decide(self.bundle, override, self.holdout, self._state, _now_us())
                k = self._state.k
                if d.would_abort and self._would_abort_step is None:
                    self._would_abort_step = k
                    self._cost_through_would_abort = self._total
                if d.action == "ABORT" and self._aborted_step is None:
                    self._aborted_step = k
                self._last_decision = d
            client.stats.checks += 1
            if d.action == "ABORT":
                client.stats.aborts += 1
            return d
        except Exception:
            client.stats.sdk_errors += 1
            log.debug("sunkcost: check failed", exc_info=True)
            return SDK_ERROR

    def record_step(
        self,
        kind: str,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal | str | int | float | None = None,
        tool_name: str | None = None,
        tool_args: Any = None,
        error: bool = False,
        latency_ms: int | None = None,
    ) -> None:
        """Record a completed step. Tool arguments are HMAC-hashed here and never sent."""
        if self._degraded:
            return
        client = self._client
        try:
            if self._ended:
                raise RuntimeError("record_step after end()")
            if kind not in STEP_KINDS:
                raise ValueError(f"kind must be 'model' or 'tool', got {kind!r}")
            ts_us = _now_us()
            model_s, tool_s = _text(model), _text(tool_name)
            tin, tout = _count(input_tokens, MAX_TOKENS), _count(output_tokens, MAX_TOKENS)
            cost = to_money(cost_usd) if cost_usd is not None else None
            if cost_usd is not None and cost is None:
                client.stats.sdk_errors += 1  # invalid amount: recorded as unknown
            if cost is None and cost_usd is None and model_s is not None:
                day = datetime.fromtimestamp(ts_us / 1e6, tz=timezone.utc).date()
                cost = client._prices.cost(model_s, tin, tout, day)
            hashed = None
            if tool_args is not None:
                try:
                    hashed = args_hash(client._salt, tool_args)
                except (CanonicalJSONError, TypeError, ValueError):
                    hashed = args_hash(client._salt, repr(tool_args))
            cost_str = None if cost is None else money_str(cost)
            step = StepRecord(
                kind=kind,
                model=model_s,
                tool_name=tool_s,
                args_hash=hashed,
                input_tokens=tin,
                output_tokens=tout,
                cost=0.0 if cost_str is None else float(cost_str),
                error=bool(error),
                ts_us=ts_us,
            )
            with self._lock:
                if self._state.k >= MAX_STEPS:
                    raise ValueError("run exceeds 1,000,000 steps")
                self._state.update(step)
                seq = self._state.k
                if cost is not None:
                    self._total += cost
                decision, self._last_decision = self._last_decision, None
            client._emit(
                {
                    "type": "step",
                    "run_id": self.run_id,
                    "seq": seq,
                    "ts": format_timestamp_us(ts_us),
                    "kind": kind,
                    "model": model_s,
                    "tool_name": tool_s,
                    "args_hash": hashed,
                    "input_tokens": tin,
                    "output_tokens": tout,
                    "cost_usd": cost_str,
                    "error": bool(error),
                    "latency_ms": None
                    if latency_ms is None
                    else _count(latency_ms, MAX_LATENCY_MS),
                    "decision": None if decision is None else decision.step_json(),
                }
            )
        except Exception:
            client.stats.sdk_errors += 1
            log.debug("sunkcost: record_step failed", exc_info=True)

    def end(self, status: str = "completed") -> None:
        """End the run; emits ``run_end`` with its decision summary. Later calls are ignored."""
        if self._degraded or self._ended:
            return
        client = self._client
        try:
            if status not in RUN_STATUSES:
                client.stats.sdk_errors += 1
                status = "errored"
            with self._lock:
                self._ended = True
                event = {
                    "type": "run_end",
                    "run_id": self.run_id,
                    "ended_at": format_timestamp_us(_now_us()),
                    "status": status,
                    "total_cost_usd": money_str(self._total),
                    "steps": self._state.k,
                    "would_abort_step": self._would_abort_step,
                    "cost_through_would_abort_usd": (
                        None
                        if self._cost_through_would_abort is None
                        else money_str(self._cost_through_would_abort)
                    ),
                    "aborted_step": self._aborted_step,
                }
            client._emit(event)
        except Exception:
            client.stats.sdk_errors += 1
            log.debug("sunkcost: end failed", exc_info=True)

    def outcome(self, outcome: str, source: str = "sdk") -> None:
        if self._degraded:
            return
        self._client.outcome(self.run_id, outcome, source)

    def __enter__(self) -> Run:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *rest: object) -> None:
        if exc_type is None:
            self.end("completed")
        elif issubclass(exc_type, RunAborted):
            self.end("aborted")
        else:
            self.end("errored")


# -- process exit: bounded flush of every open client (§4 C2) -----------------------------------

_clients: weakref.WeakSet[Sunkcost] = weakref.WeakSet()
_exit_registered = False
_exit_lock = threading.Lock()


def _close_all() -> None:
    for client in list(_clients):
        client.close()


def _register_exit(client: Sunkcost) -> None:
    global _exit_registered
    with _exit_lock:
        _clients.add(client)
        if not _exit_registered:
            atexit.register(_close_all)
            _exit_registered = True
