"""Signed webhook delivery (ARCHITECTURE.md §5.1 ⑧).

``X-Sunkcost-Signature: t=<unix seconds>,v1=<hex HMAC_SHA256(secret, "<t>.<body>")>``. A job retries
delivery 5 times over about an hour, then records it as failed. Private, loopback and link-local
addresses are refused (no SSRF into the worker's network) unless explicitly allowed for local
development.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

# Delays before attempts 2..5: about one hour in total.
RETRY_DELAYS_S = (60, 300, 900, 2100)
MAX_ATTEMPTS = len(RETRY_DELAYS_S) + 1


class WebhookError(Exception):
    pass


def signature(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify(
    secret: str, header: str, body: bytes, *, tolerance_s: int = 300, now: int | None = None
) -> bool:
    """Receiver side, for customers and tests: check the signature and its freshness."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs((now or int(time.time())) - ts) > tolerance_s:
        return False
    expected = signature(secret, ts, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def check_destination(url: str, *, allow_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" and not allow_private:
        raise WebhookError("webhook URLs must use https")
    if not parts.hostname:
        raise WebhookError("webhook URL has no host")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise WebhookError(f"cannot resolve {parts.hostname}: {exc}") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise WebhookError(f"{parts.hostname} resolves to a non-public address")


def deliver(
    url: str,
    secret: str,
    event: dict[str, Any],
    *,
    timeout_s: float,
    allow_private: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """POST one event; returns the HTTP status. Raises WebhookError on a retryable failure."""
    check_destination(url, allow_private=allow_private)
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "sunkcost-webhooks/1",
        "X-Sunkcost-Signature": signature(secret, int(time.time()), body),
        "X-Sunkcost-Event": str(event.get("type", "")),
    }
    try:
        with httpx.Client(timeout=timeout_s, transport=transport, follow_redirects=False) as client:
            response = client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise WebhookError(f"delivery failed: {type(exc).__name__}") from None
    if response.status_code >= 300:
        raise WebhookError(f"receiver answered {response.status_code}")
    return response.status_code
