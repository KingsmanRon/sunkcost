"""RFC 9457 problem details. Every error response is ``application/problem+json``."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_TYPE = "https://sunkcost.dev/problems/"
TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class Problem(Exception):  # noqa: N818 - an HTTP problem document, raised to return it
    def __init__(
        self,
        status: int,
        code: str,
        detail: str = "",
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status, self.code, self.detail = status, code, detail
        self.headers = headers or {}
        self.extra = extra or {}


def problem_response(request: Request, problem: Problem) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE + problem.code,
        "title": TITLES.get(problem.status, "Error"),
        "status": problem.status,
        "detail": problem.detail or problem.code.replace("-", " "),
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
        **problem.extra,
    }
    return JSONResponse(
        body,
        status_code=problem.status,
        headers=problem.headers,
        media_type="application/problem+json",
    )


async def problem_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, Problem):
        exc = Problem(500, "internal-error", "unexpected server error")
    return problem_response(request, exc)


async def http_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return await problem_handler(request, exc)
    code = {404: "not-found", 405: "method-not-allowed"}.get(exc.status_code, "http-error")
    return problem_response(request, Problem(exc.status_code, code, str(exc.detail)))


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return await problem_handler(request, exc)
    errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()][:20]
    return problem_response(
        request,
        Problem(422, "invalid-request", "request validation failed", extra={"errors": errors}),
    )
