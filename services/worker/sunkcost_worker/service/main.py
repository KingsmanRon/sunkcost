"""Worker process: a job-runner loop, a scheduler loop and an internal /healthz (§4 C7, §11.2).

Only the worker holding the Postgres advisory lock schedules, so it can scale out safely. The lock
is held on a dedicated session connection, which is why the worker uses the session pooler.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg

from sunkcost_worker.service.config import WorkerSettings
from sunkcost_worker.service.db import WorkerDb
from sunkcost_worker.service.jobs import SCHEDULER_LOCK, Worker
from sunkcost_worker.service.signing import Signer

log = logging.getLogger("sunkcost.worker")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("job_id", "type", "attempt", "dead", "error", "s", "workload_id"):
            if hasattr(record, key):
                doc[key] = getattr(record, key)
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc)


class SchedulerLock:
    """``pg_try_advisory_lock`` on its own session; re-acquired if the connection drops."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._conn: psycopg.Connection[Any] | None = None

    def held(self) -> bool:
        try:
            if self._conn is None or self._conn.closed:
                self._conn = psycopg.connect(self._url, autocommit=True, prepare_threshold=None)
                row = self._conn.execute(
                    "select pg_try_advisory_lock(%s)", (SCHEDULER_LOCK,)
                ).fetchone()
                if not (row and row[0]):
                    self._conn.close()
                    self._conn = None
                    return False
            self._conn.execute("select 1")
            return True
        except psycopg.Error:
            self._conn = None
            return False

    def release(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()


def serve_health(worker: Worker, port: int, poll_interval_s: float) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            age = (
                (time.time_ns() // 1000 - worker.last_poll_us) / 1e6
                if worker.last_poll_us
                else None
            )
            healthy = age is not None and age < max(60.0, 10 * poll_interval_s)
            body = json.dumps(
                {
                    "status": "ok" if healthy else "stale",
                    "last_poll_age_s": age,
                    "scheduler": worker.is_scheduler,
                }
            ).encode()
            self.send_response(200 if healthy and self.path == "/healthz" else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - internal port only
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:  # pragma: no cover - process entry point
    settings = WorkerSettings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[handler])

    db = WorkerDb(settings.database_url)
    worker = Worker(
        db,
        Signer(settings.signing_key_b64, settings.signing_key_id),
        webhook_timeout_s=settings.webhook_timeout_s,
        webhook_allow_private=settings.webhook_allow_private,
    )
    worker.publish_signing_key()
    lock = SchedulerLock(settings.database_url)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    server = serve_health(worker, settings.health_port, settings.poll_interval_s)
    log.info("worker started", extra={"type": "start"})

    next_schedule = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_schedule:
                worker.is_scheduler = lock.held()
                if worker.is_scheduler:
                    worker.schedule()
                next_schedule = time.monotonic() + settings.schedule_interval_s
            ran = worker.run_due_jobs()
        except Exception:
            log.exception("worker loop error")
            ran = 0
        if not ran:
            stop.wait(settings.poll_interval_s)
    lock.release()
    server.shutdown()
    db.close()
    log.info("worker stopped")


if __name__ == "__main__":  # pragma: no cover
    main()
