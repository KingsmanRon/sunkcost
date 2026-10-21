"""Bundle manager: background refresh, verification, rollback protection and disk cache (§4 C2).

``get`` is a dictionary read with no I/O, so ``check()`` stays network-free. A background thread
fetches each registered workload's bundle at start-up and every ``refresh_s`` seconds with
``If-None-Match``; failures back off from 5 s up to ``refresh_s``. Every accepted bundle raises the
workload's version floor, so an older version is refused (rollback protection), including across
restarts when ``cache_dir`` is set.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sunkcost.bundle import Bundle, BundleRejected, verify_response
from sunkcost.transport import Transport

log = logging.getLogger("sunkcost")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
MIN_RETRY_S = 5.0


@dataclass(slots=True)
class _Entry:
    bundle: Bundle | None = None
    override: str | None = None
    etag: str | None = None
    floor: int = 0  # highest version accepted so far
    next_fetch: float = 0.0
    failures: int = 0


class BundleManager:
    def __init__(
        self,
        transport: Transport | None,
        trusted_keys: Mapping[str, Ed25519PublicKey],
        *,
        refresh_s: float = 300.0,
        cache_dir: str | os.PathLike[str] | None = None,
        clock_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self._transport = transport
        self._keys = dict(trusted_keys)
        self._refresh_s = refresh_s
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._clock_us = clock_us
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.rejected = 0
        self.fetch_errors = 0
        self._thread = threading.Thread(target=self._run, name="sunkcost-bundles", daemon=True)

    # -- read side (hot path) ---------------------------------------------------------------

    def get(self, workload: str) -> tuple[Bundle | None, str | None]:
        entry = self._entries.get(workload)
        if entry is None:
            return None, None
        return entry.bundle, entry.override

    def override(self, workload: str) -> str | None:
        entry = self._entries.get(workload)
        return None if entry is None else entry.override

    # -- registration -----------------------------------------------------------------------

    def register(self, workload: str) -> None:
        """Start tracking a workload: load its disk cache now, fetch in the background."""
        if workload in self._entries:
            return
        with self._lock:
            if workload in self._entries:
                return
            entry = _Entry()
            self._load_cache(workload, entry)
            self._entries[workload] = entry
        self._wake.set()

    def start(self) -> None:
        if self._transport is not None:
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- fetching ---------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            due = [w for w, e in list(self._entries.items()) if e.next_fetch <= now]
            for workload in due:
                if self._stop.is_set():
                    return
                self.refresh(workload)
            upcoming = [e.next_fetch for e in list(self._entries.values())]
            wait = min(upcoming) - time.monotonic() if upcoming else self._refresh_s
            self._wake.wait(max(0.05, min(wait, self._refresh_s)))
            self._wake.clear()

    def refresh(self, workload: str) -> bool:
        """Fetch once now. Returns True if the workload's state changed."""
        self.register(workload)
        entry = self._entries[workload]
        if self._transport is None:
            return False
        try:
            result = self._transport.fetch_bundle(workload, entry.etag)
        except Exception:
            self.fetch_errors += 1
            self._schedule_retry(entry)
            log.debug("sunkcost: bundle fetch for %s failed", workload, exc_info=True)
            return False
        if result.status == 304:
            entry.failures, entry.next_fetch = 0, time.monotonic() + self._refresh_s
            return False
        if result.status == 404:
            # No active bundle: dropping the cached one can only reduce enforcement.
            changed = entry.bundle is not None
            entry.bundle, entry.override, entry.etag = None, None, None
            entry.failures, entry.next_fetch = 0, time.monotonic() + self._refresh_s
            self._delete_cache(workload)
            return changed
        if result.status != 200:
            self.fetch_errors += 1
            self._schedule_retry(entry)
            return False
        try:
            doc = json.loads(result.body)
            bundle, override = verify_response(
                doc, self._keys, workload=workload, now_us=self._clock_us(), min_version=entry.floor
            )
        except (BundleRejected, ValueError) as exc:
            self.rejected += 1
            log.warning("sunkcost: rejected bundle for %s: %s", workload, exc)
            self._schedule_retry(entry)
            return False
        with self._lock:
            if bundle.version < entry.floor:
                return False  # a newer version was installed while this fetch was in flight
            entry.bundle, entry.override, entry.etag = bundle, override, result.etag
            entry.floor = max(entry.floor, bundle.version)
            entry.failures, entry.next_fetch = 0, time.monotonic() + self._refresh_s
        self._save_cache(workload, doc)
        return True

    def _schedule_retry(self, entry: _Entry) -> None:
        entry.failures += 1
        delay = min(self._refresh_s, MIN_RETRY_S * 2 ** (entry.failures - 1))
        entry.next_fetch = time.monotonic() + delay

    def install(self, workload: str, doc: Any) -> None:
        """Verify and install a bundle response directly (offline use and tests)."""
        self.register(workload)
        entry = self._entries[workload]
        bundle, override = verify_response(
            doc, self._keys, workload=workload, now_us=self._clock_us(), min_version=entry.floor
        )
        with self._lock:
            if bundle.version < entry.floor:
                raise BundleRejected("rollback", f"version {bundle.version} < {entry.floor}")
            entry.bundle, entry.override = bundle, override
            entry.floor = max(entry.floor, bundle.version)

    # -- disk cache (SUNKCOST_CACHE_DIR) ------------------------------------------------------

    def _cache_path(self, workload: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"bundle-{_SAFE_NAME.sub('_', workload)}.json"

    def _load_cache(self, workload: str, entry: _Entry) -> None:
        path = self._cache_path(workload)
        if path is None or not path.exists():
            return
        try:
            doc = json.loads(path.read_text("utf-8"))
            floor = int(doc.get("_sunkcost_floor", 0)) if isinstance(doc, dict) else 0
            entry.floor = max(entry.floor, floor)
            bundle, override = verify_response(
                doc, self._keys, workload=workload, now_us=self._clock_us(), min_version=entry.floor
            )
        except (OSError, ValueError, BundleRejected) as exc:
            log.info("sunkcost: ignoring cached bundle for %s: %s", workload, exc)
            return
        entry.bundle, entry.override = bundle, override
        entry.floor = max(entry.floor, bundle.version)

    def _save_cache(self, workload: str, doc: Any) -> None:
        path = self._cache_path(workload)
        if path is None:
            return
        entry = self._entries[workload]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(doc, _sunkcost_floor=entry.floor)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".bundle-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError:
            log.debug("sunkcost: could not write bundle cache", exc_info=True)

    def _delete_cache(self, workload: str) -> None:
        path = self._cache_path(workload)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
