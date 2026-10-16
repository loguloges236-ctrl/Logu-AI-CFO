"""Edge ASGI middleware: proxy resolution, host allow-listing, HTTPS enforcement and security headers.

Pure ASGI (no framework imports) so every rule can be exercised with a fake application.

Design notes:
- No HTTP->HTTPS *redirect* is issued here. TLS redirection belongs to the edge proxy (Caddy).
  A redirect from the backend would loop behind a TLS-terminating proxy whose upstream hop is HTTP.
  Instead, a request that a *trusted* proxy reports as plain HTTP is rejected with 400 when
  FORCE_HTTPS is enabled. Direct internal HTTP traffic (for example, the frontend server calling the
  API on the private network) carries no forwarded headers and is not rejected.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.security.proxy import (
    PEER_IP_KEY,
    TRUSTED_SCHEME_KEY,
    Network,
    apply_proxy_resolution,
    is_trusted_peer,
)

Scope = dict[str, Any]
Message = dict[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]]

HEALTH_PATHS = frozenset({"/health", "/health/ready"})
API_DOC_PATHS = frozenset({"/docs", "/openapi.json"})
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")
REQUEST_ID_KEY = "logu.request_id"

_CSP_STRICT = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def normalize_host(raw_host: str) -> str:
    host = raw_host.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        return host[1 : host.find("]")] if "]" in host else host
    return host.split(":", 1)[0]


class EdgeMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_networks: Sequence[Network],
        trusted_hosts: Sequence[str],
        force_https: bool,
        expose_api_docs: bool,
        internal_hosts: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.trusted_networks = list(trusted_networks)
        self.trusted_hosts = {host.lower() for host in trusted_hosts}
        # Single-label service names (for example "backend") used by the frontend container
        # for server-side calls. Accepted ONLY when the request did not pass through a trusted
        # proxy, so a client behind Caddy cannot use them to get past the public Host check.
        self.internal_hosts = {host.lower() for host in internal_hosts}
        self.force_https = force_https
        self.expose_api_docs = expose_api_docs

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        scope = apply_proxy_resolution(scope, self.trusted_networks)
        scope[REQUEST_ID_KEY] = request_id
        path = scope.get("path", "")
        is_health = path in HEALTH_PATHS

        if not is_health:
            host = _header(scope, b"host")
            normalized = normalize_host(host) if host else ""
            host_allowed = normalized in self.trusted_hosts or (
                normalized in self.internal_hosts
                and not is_trusted_peer(scope.get(PEER_IP_KEY), self.trusted_networks)
            )
            if not host or not host_allowed:
                await _reject(send, 400, "invalid_host", "Request host is not permitted.", request_id)
                return
            if self.force_https and scope.get(TRUSTED_SCHEME_KEY) == "http":
                await _reject(send, 400, "https_required", "HTTPS is required.", request_id)
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": _security_headers(
                    list(message.get("headers", [])),
                    request_id=request_id,
                    https=scope.get("scheme") == "https",
                    force_https=self.force_https,
                    relaxed_csp=self.expose_api_docs and path in API_DOC_PATHS,
                )}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _request_id(scope: Scope) -> str:
    incoming = _header(scope, b"x-request-id") or ""
    return incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _security_headers(
    headers: list[tuple[bytes, bytes]],
    *,
    request_id: str,
    https: bool,
    force_https: bool,
    relaxed_csp: bool,
) -> list[tuple[bytes, bytes]]:
    additions: dict[bytes, str] = {
        b"x-request-id": request_id,
        b"x-content-type-options": "nosniff",
        b"x-frame-options": "DENY",
        b"referrer-policy": "no-referrer",
        b"permissions-policy": _PERMISSIONS_POLICY,
        b"cross-origin-opener-policy": "same-origin",
        b"cross-origin-resource-policy": "same-origin",
        b"cache-control": "no-store",
    }
    if not relaxed_csp:
        additions[b"content-security-policy"] = _CSP_STRICT
    if force_https or https:
        additions[b"strict-transport-security"] = "max-age=63072000"
    # Our policy values override anything the application set for the same header names.
    result = [(key, value) for key, value in headers if key.lower() not in additions]
    result.extend((key, value.encode("latin-1")) for key, value in additions.items())
    return result


async def _reject(send: Any, status: int, code: str, message: str, request_id: str) -> None:
    body = json.dumps(
        {"error": {"code": code, "message": message, "details": {}, "request_id": request_id}}
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    await send({"type": "http.response.start", "status": status, "headers": _security_headers(
        headers, request_id=request_id, https=False, force_https=False, relaxed_csp=False
    )})
    await send({"type": "http.response.body", "body": body})
