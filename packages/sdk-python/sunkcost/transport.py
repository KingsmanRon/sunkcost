"""HTTP transport for the two SDK → API calls (§5.1 ① and ②). Never used inside ``check()``."""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from sunkcost import __version__

GZIP_MIN_BYTES = 1024
USER_AGENT = f"sunkcost-python/{__version__}"

SendStatus = Literal["ok", "retry", "drop"]


@dataclass(frozen=True, slots=True)
class SendResult:
    status: SendStatus
    retry_after_s: float | None = None
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    """``status`` is 200 (new body), 304 (unchanged), 404 (no active bundle) or another code."""

    status: int
    body: bytes = b""
    etag: str | None = None


class Transport(Protocol):
    def send_batch(self, body: bytes) -> SendResult: ...

    def fetch_bundle(self, workload: str, etag: str | None) -> FetchResult: ...

    def close(self) -> None: ...


def classify(status: int) -> SendStatus:
    """2xx accepted; 408, 425, 429 and 5xx retried; any other code is permanent (batch dropped)."""
    if 200 <= status < 300:
        return "ok"
    if status in (408, 425, 429) or status >= 500:
        return "retry"
    return "drop"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class HttpTransport:
    def __init__(self, endpoint: str, api_key: str, *, timeout_s: float = 10.0) -> None:
        self._client = httpx.Client(
            base_url=endpoint.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
        )

    def send_batch(self, body: bytes) -> SendResult:
        headers = {"Content-Type": "application/json"}
        if len(body) >= GZIP_MIN_BYTES:
            body = gzip.compress(body, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
        try:
            response = self._client.post("/v1/ingest", content=body, headers=headers)
        except httpx.HTTPError:
            return SendResult("retry")
        return SendResult(
            classify(response.status_code), _retry_after(response), response.status_code
        )

    def fetch_bundle(self, workload: str, etag: str | None) -> FetchResult:
        headers = {"If-None-Match": etag} if etag else {}
        response = self._client.get(
            "/v1/bundles/active", params={"workload": workload}, headers=headers
        )
        return FetchResult(response.status_code, response.content, response.headers.get("etag"))

    def close(self) -> None:
        self._client.close()
