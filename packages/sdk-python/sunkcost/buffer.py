"""Bounded event buffer with a background flusher (§4 C2 event buffer, §10).

``put`` never blocks and never raises: when the buffer is full the oldest event is dropped and
counted. The flusher sends batches every ``interval_s`` or as soon as ``batch_size`` events are
queued, retries with exponential backoff and full jitter capped at 60 s, and reports the number of
dropped events in the next batch header (``dropped_events``).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from sunkcost import __version__
from sunkcost.core import format_timestamp_us
from sunkcost.transport import Transport

log = logging.getLogger("sunkcost")

MAX_BACKOFF_S = 60.0
BASE_BACKOFF_S = 0.5


class EventBuffer:
    def __init__(
        self,
        transport: Transport,
        *,
        capacity: int = 10_000,
        batch_size: int = 500,
        interval_s: float = 2.0,
        clock_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self._transport = transport
        self._capacity = capacity
        self._batch_size = batch_size
        self._interval_s = interval_s
        self._clock_us = clock_us
        self._queue: deque[dict[str, Any]] = deque()
        self._cond = threading.Condition()
        self._pending: list[dict[str, Any]] | None = None  # batch taken but not yet accepted
        self._dropped = 0  # dropped since the last accepted batch
        self._attempt = 0
        self._next_send = 0.0
        self._closing = False
        self._flush_requested = False
        self._close_deadline = 0.0
        self._idle = threading.Event()
        self._idle.set()
        self.sent_events = 0
        self.dropped_total = 0
        self.failed_sends = 0
        self._thread = threading.Thread(target=self._run, name="sunkcost-flush", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # -- producer side (agent threads) ------------------------------------------------------

    def put(self, event: dict[str, Any]) -> None:
        with self._cond:
            if len(self._queue) >= self._capacity:
                self._queue.popleft()
                self._dropped += 1
                self.dropped_total += 1
            self._queue.append(event)
            self._idle.clear()
            if len(self._queue) >= self._batch_size:
                self._cond.notify()

    @property
    def queued(self) -> int:
        with self._cond:
            return len(self._queue) + len(self._pending or ())

    # -- flusher ----------------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                if not self._closing:
                    self._cond.wait_for(
                        lambda: (
                            self._closing
                            or self._flush_requested
                            or len(self._queue) >= self._batch_size
                        ),
                        timeout=self._interval_s,
                    )
                closing = self._closing
                self._flush_requested = False
            self._flush_once()
            if closing and (self.queued == 0 or time.monotonic() >= self._close_deadline):
                return
            if closing:
                delay = self._next_send - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 0.05))

    def _flush_once(self) -> None:
        """Send queued batches until the queue is empty or a send has to back off."""
        while True:
            if time.monotonic() < self._next_send:
                return
            with self._cond:
                if self._pending is None:
                    if not self._queue:
                        self._idle.set()
                        return
                    n = min(self._batch_size, len(self._queue))
                    self._pending = [self._queue.popleft() for _ in range(n)]
                batch, dropped = self._pending, self._dropped
            body = json.dumps(
                {
                    "schema_version": "1",
                    "sent_at": format_timestamp_us(self._clock_us()),
                    "sdk": {"name": "sunkcost-python", "version": __version__},
                    "dropped_events": dropped,
                    "events": batch,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                result = self._transport.send_batch(body)
                status, retry_after = result.status, result.retry_after_s
            except Exception:  # a transport bug must never kill the flusher
                log.debug("sunkcost: ingest send raised", exc_info=True)
                status, retry_after = "retry", None
            with self._cond:
                if status == "ok":
                    self._dropped -= dropped
                    self.sent_events += len(batch)
                    self._pending, self._attempt = None, 0
                elif status == "drop":
                    log.warning("sunkcost: ingest rejected a batch of %d events", len(batch))
                    self._dropped += len(batch)
                    self.dropped_total += len(batch)
                    self.failed_sends += 1
                    self._pending, self._attempt = None, 0
                else:
                    self.failed_sends += 1
                    self._attempt += 1
                    cap = min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2**self._attempt)
                    delay = random.uniform(0, cap)  # noqa: S311 - jitter, not crypto
                    if retry_after is not None:
                        delay = max(delay, min(retry_after, MAX_BACKOFF_S))
                    self._next_send = time.monotonic() + delay
                    return

    # -- lifecycle --------------------------------------------------------------------------

    def flush(self, timeout_s: float) -> bool:
        """Wake the flusher and wait up to ``timeout_s`` for the buffer to drain."""
        with self._cond:
            self._next_send = 0.0
            self._flush_requested = True
            self._cond.notify()
        if not self._thread.is_alive():
            return self.queued == 0
        return self._idle.wait(timeout_s) and self.queued == 0

    def close(self, timeout_s: float) -> bool:
        """Final flush, waiting at most ``timeout_s``; returns True if everything was sent."""
        with self._cond:
            self._closing = True
            self._close_deadline = time.monotonic() + timeout_s
            self._next_send = 0.0
            self._cond.notify()
        if self._thread.is_alive():
            self._thread.join(timeout_s)
        return self.queued == 0
