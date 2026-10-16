"""Edge behaviour for the frontend's server-side calls to the API on the private network.

The frontend container calls http://backend:8000 directly. That request has:
- a TCP peer that is NOT a trusted proxy (the frontend container, not Caddy),
- no X-Forwarded-* headers,
- Host: backend:8000 (the internal service name).

These tests pin down which requests are accepted, and make sure the internal host name cannot
be used to bypass the public Host allow-list or the HTTPS rule when a request arrives through Caddy.
"""

import asyncio
import unittest

from app.security.edge import EdgeMiddleware
from app.security.proxy import parse_networks

CADDY = "172.28.10.10"
FRONTEND = "172.28.10.20"
OUTSIDE = "203.0.113.5"
PUBLIC_HOST = "cfo.example.com"
INTERNAL_HOST = "backend"


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope(peer, host, headers=(), path="/api/v1/auth/me"):
    raw = [(b"host", host.encode())]
    raw.extend((name.encode(), value.encode()) for name, value in headers)
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw,
        "client": (peer, 50000),
        "scheme": "http",
        "query_string": b"",
    }


def _run(middleware, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(scope, receive, send))
    status = messages[0]["status"]
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return status, body


class InternalApiAccessTests(unittest.TestCase):
    def middleware(self, internal_hosts=(INTERNAL_HOST,)):
        return EdgeMiddleware(
            _ok_app,
            trusted_networks=parse_networks([f"{CADDY}/32"]),
            trusted_hosts=[PUBLIC_HOST, "localhost"],
            force_https=True,
            expose_api_docs=False,
            internal_hosts=internal_hosts,
        )

    def test_frontend_ssr_call_is_accepted_under_force_https(self):
        status, _ = _run(self.middleware(), _scope(FRONTEND, f"{INTERNAL_HOST}:8000"))
        self.assertEqual(status, 200)

    def test_internal_host_is_not_accepted_when_not_configured(self):
        status, body = _run(
            self.middleware(internal_hosts=()), _scope(FRONTEND, f"{INTERNAL_HOST}:8000")
        )
        self.assertEqual(status, 400)
        self.assertIn(b"invalid_host", body)

    def test_internal_host_name_cannot_be_used_through_caddy(self):
        scope = _scope(CADDY, f"{INTERNAL_HOST}:8000", [("x-forwarded-proto", "https")])
        status, body = _run(self.middleware(), scope)
        self.assertEqual(status, 400)
        self.assertIn(b"invalid_host", body)

    def test_public_host_through_caddy_with_https_is_accepted(self):
        scope = _scope(CADDY, PUBLIC_HOST, [("x-forwarded-proto", "https")])
        status, _ = _run(self.middleware(), scope)
        self.assertEqual(status, 200)

    def test_plain_http_through_caddy_is_still_rejected(self):
        scope = _scope(CADDY, PUBLIC_HOST, [("x-forwarded-proto", "http")])
        status, body = _run(self.middleware(), scope)
        self.assertEqual(status, 400)
        self.assertIn(b"https_required", body)

    def test_unknown_host_is_still_rejected_from_frontend(self):
        status, body = _run(self.middleware(), _scope(FRONTEND, "evil.example.net"))
        self.assertEqual(status, 400)
        self.assertIn(b"invalid_host", body)

    def test_forged_forwarded_proto_from_outside_does_not_flip_scheme(self):
        # An untrusted peer's X-Forwarded-Proto is ignored, so it cannot trigger or clear HTTPS rules.
        scope = _scope(OUTSIDE, PUBLIC_HOST, [("x-forwarded-proto", "https")])
        status, body = _run(self.middleware(), scope)
        self.assertEqual(status, 200)
        self.assertNotIn(b"https_required", body)


if __name__ == "__main__":
    unittest.main()
