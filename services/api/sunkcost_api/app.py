"""The Sunkcost API (ARCHITECTURE.md §4 C6). It never calls the worker: it enqueues jobs."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from sunkcost_api import routes_console, routes_sdk
from sunkcost_api.auth import JwtAuthenticator, KeyAuthenticator
from sunkcost_api.common import ORG_HEADER, Services, jsonable, services
from sunkcost_api.config import Settings
from sunkcost_api.db import Database, rows
from sunkcost_api.problems import Problem, http_handler, problem_handler, validation_handler
from sunkcost_api.ratelimit import RateLimiter

log = logging.getLogger("sunkcost.api")

REQUESTS = Counter("sunkcost_api_requests_total", "HTTP requests", ["route", "status"])
LATENCY = Histogram("sunkcost_api_request_seconds", "HTTP request latency", ["route"])

# Routes for SDKs and business systems: browsers are refused (§9 Transport).
API_KEY_PATHS = ("/v1/ingest", "/v1/outcomes", "/v1/bundles/active")
SIGNING_KEYS_SQL = (
    "select key_id, public_key_b64, created_at, retired_at from public.signing_keys "
    "where retired_at is null or retired_at > now() - interval '30 days' order by created_at desc"
)


class JsonFormatter(logging.Formatter):
    """Structured logs. Request and response bodies are never logged."""

    FIELDS = ("request_id", "route", "status", "ms")

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        doc.update({k: getattr(record, k) for k in self.FIELDS if hasattr(record, k)})
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(
        settings.database_url, min_size=settings.db_pool_min, max_size=settings.db_pool_max
    )
    jwt_auth = JwtAuthenticator(
        jwks_url=settings.supabase_jwks_url, secret=settings.supabase_jwt_secret
    )
    svc = Services(
        settings=settings,
        db=db,
        keys=KeyAuthenticator(db),
        jwt=jwt_auth,
        limiter=RateLimiter(settings.rate_limit_per_min),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.open()
        yield
        await db.close()

    app = FastAPI(
        title="Sunkcost API", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.services = svc
    app.add_exception_handler(Problem, problem_handler)
    app.add_exception_handler(StarletteHTTPException, http_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.include_router(routes_sdk.router)
    app.include_router(routes_console.router)

    if settings.console_origin:
        # CORS matters only for the console's JWT routes; the API-key routes refuse browsers.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.console_origin],
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", ORG_HEADER],
            expose_headers=["x-request-id"],
        )

    @app.middleware("http")
    async def envelope(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        path = request.url.path
        t0 = time.perf_counter()
        try:
            if path in API_KEY_PATHS and request.headers.get("origin"):
                raise Problem(
                    403, "browser-origin", "this endpoint does not accept browser requests"
                )
            response = await call_next(request)
        except Problem as problem:
            response = await problem_handler(request, problem)
        except Exception:
            log.exception("unhandled error", extra={"request_id": request.state.request_id})
            error = Problem(500, "internal-error", "unexpected server error")
            response = await problem_handler(request, error)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["strict-transport-security"] = "max-age=63072000; includeSubDomains"
        response.headers["x-content-type-options"] = "nosniff"
        route = path if response.status_code != 404 else "unmatched"
        elapsed = time.perf_counter() - t0
        REQUESTS.labels(route, str(response.status_code)).inc()
        LATENCY.labels(route).observe(elapsed)
        extra = {
            "request_id": request.state.request_id,
            "route": route,
            "status": response.status_code,
            "ms": round(elapsed * 1000, 1),
        }
        log.info("request", extra=extra)
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        if not await services(request).db.ping():
            unavailable = Problem(
                503,
                "database-unavailable",
                "the database is not reachable",
                headers={"Retry-After": "5"},
            )
            raise unavailable
        return JSONResponse({"status": "ready"})

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/signing-keys")
    async def signing_keys(request: Request) -> dict[str, Any]:
        async with services(request).db.tx() as conn:
            found = await rows(conn, SIGNING_KEYS_SQL)
        return {"keys": [jsonable(r) for r in found]}

    return app


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    settings = Settings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[handler])
    host = "0.0.0.0"  # noqa: S104 - inside the container; the platform routes to it
    port = int(os.environ.get("PORT", "8000"))  # Railway injects PORT and health-checks it
    uvicorn.run(create_app(settings), host=host, port=port, proxy_headers=True, access_log=False)


if __name__ == "__main__":  # pragma: no cover
    main()
