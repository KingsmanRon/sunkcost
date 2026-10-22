"""Worker configuration from the environment (ARCHITECTURE.md §11.3). No secret has a default."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str  # session pooler (port 5432), role sunkcost_worker
    signing_key_b64: str  # base64 of the 32-byte Ed25519 seed
    signing_key_id: str
    webhook_timeout_s: float = 10.0
    poll_interval_s: float = 2.0
    schedule_interval_s: float = 60.0
    health_port: int = 8081
    log_level: str = "INFO"
    webhook_allow_private: bool = False  # local development only: deliver to private addresses

    @classmethod
    def from_env(cls) -> WorkerSettings:
        missing = [
            k
            for k in ("DATABASE_URL", "BUNDLE_SIGNING_KEY", "BUNDLE_SIGNING_KEY_ID")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            database_url=os.environ["DATABASE_URL"],
            signing_key_b64=os.environ["BUNDLE_SIGNING_KEY"],
            signing_key_id=os.environ["BUNDLE_SIGNING_KEY_ID"],
            webhook_timeout_s=float(os.environ.get("WEBHOOK_TIMEOUT_S", "10")),
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "2")),
            schedule_interval_s=float(os.environ.get("SCHEDULE_INTERVAL_S", "60")),
            health_port=int(os.environ.get("HEALTH_PORT", "8081")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            webhook_allow_private=os.environ.get("WEBHOOK_ALLOW_PRIVATE") == "1",
        )
