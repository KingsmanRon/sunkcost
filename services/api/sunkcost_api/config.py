"""Configuration from the environment (ARCHITECTURE.md §11.3). Nothing secret has a default."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    supabase_url: str | None
    supabase_jwt_secret: str | None
    supabase_jwks_url: str | None
    console_origin: str | None
    log_level: str = "INFO"
    rate_limit_per_min: int = 600
    max_body_bytes: int = 1_000_000
    db_pool_min: int = 1
    db_pool_max: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is required (role sunkcost_api, transaction pooler)")
        supabase_url = os.environ.get("SUPABASE_URL") or None
        jwks = os.environ.get("SUPABASE_JWKS_URL") or (
            f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json" if supabase_url else None
        )
        return cls(
            database_url=url,
            supabase_url=supabase_url,
            supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
            supabase_jwks_url=jwks,
            console_origin=os.environ.get("CONSOLE_ORIGIN") or None,
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "600")),
            db_pool_min=int(os.environ.get("DB_POOL_MIN", "1")),
            db_pool_max=int(os.environ.get("DB_POOL_MAX", "10")),
        )
