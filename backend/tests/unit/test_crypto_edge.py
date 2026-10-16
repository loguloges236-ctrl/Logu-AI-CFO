import unittest

from cryptography.fernet import Fernet

from app.security.crypto import CryptoError, SensitiveDataCipher
from app.security.edge import EdgeMiddleware, normalize_host
from app.security.proxy import parse_networks


class CipherTests(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode()
        self.cipher = SensitiveDataCipher(self.key)

    def test_bytes_round_trip(self):
        self.assertEqual(self.cipher.decrypt_bytes(self.cipher.encrypt_bytes(b"invoice-42")), b"invoice-42")

    def test_text_round_trip(self):
        self.assertEqual(self.cipher.decrypt_text(self.cipher.encrypt_text("பில்")), "பில்")

    def test_tampered_ciphertext_is_rejected(self):
        token = bytearray(self.cipher.encrypt_bytes(b"secret"))
        token[-5] ^= 0x01
        with self.assertRaises(CryptoError):
            self.cipher.decrypt_bytes(bytes(token))

    def test_wrong_key_is_rejected(self):
        other = SensitiveDataCipher(Fernet.generate_key().decode())
        with self.assertRaises(CryptoError):
            other.decrypt_bytes(self.cipher.encrypt_bytes(b"secret"))

    def test_missing_or_malformed_key_fails_closed_without_leaking_it(self):
        with self.assertRaises(CryptoError):
            SensitiveDataCipher("")
        bad = "definitely-not-a-key"
        with self.assertRaises(CryptoError) as ctx:
            SensitiveDataCipher(bad)
        self.assertNotIn(bad, str(ctx.exception))


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"cache-control", b"public")]})
    await send({"type": "http.response.body", "body": b"ok"})


class FakeReceive:
    async def __call__(self):
        return {"type": "http.request", "body": b"", "more_body": False}


async def _run(middleware, scope):
    messages = []

    async def send(message):
        messages.append(message)

    await middleware(scope, FakeReceive(), send)
    return messages


def _scope(path="/api/v1/auth/me", host="cfo.example.com", peer="172.28.10.10", extra_headers=()):
    headers = [(b"host", host.encode())] + [(k.encode(), v.encode()) for k, v in extra_headers]
    return {"type": "http", "path": path, "client": (peer, 1234), "scheme": "http", "headers": headers}


def _status_and_headers(messages):
    start = messages[0]
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}


class EdgeMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **overrides):
        options = {
            "trusted_networks": parse_networks(["172.28.10.10/32"]),
            "trusted_hosts": ["cfo.example.com"],
            "force_https": True,
            "expose_api_docs": False,
        }
        options.update(overrides)
        return EdgeMiddleware(_ok_app, **options)

    async def test_allowed_host_passes_with_security_headers(self):
        status, headers = _status_and_headers(await _run(self.make(), _scope(extra_headers=[("x-forwarded-proto", "https")])))
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertIn("strict-transport-security", headers)

    async def test_unknown_host_is_rejected(self):
        status, _ = _status_and_headers(await _run(self.make(), _scope(host="evil.example")))
        self.assertEqual(status, 400)

    async def test_health_bypasses_host_check_for_probes(self):
        status, _ = _status_and_headers(await _run(self.make(), _scope(path="/health", host="127.0.0.1:8000")))
        self.assertEqual(status, 200)

    async def test_trusted_proxy_reporting_http_is_rejected_when_https_forced(self):
        status, body_headers = _status_and_headers(
            await _run(self.make(), _scope(extra_headers=[("x-forwarded-proto", "http")]))
        )
        self.assertEqual(status, 400)
        self.assertNotIn("location", body_headers)  # never a redirect, so no redirect loops

    async def test_internal_plain_http_without_forwarded_headers_is_allowed(self):
        # Frontend server-side calls on the private network carry no forwarded headers.
        status, _ = _status_and_headers(await _run(self.make(), _scope(peer="172.28.20.5")))
        self.assertEqual(status, 200)

    async def test_untrusted_peer_cannot_force_rejection_by_setting_headers_from_outside(self):
        # Only the scheme-based rule is affected; the request still reaches the app with its own address.
        status, _ = _status_and_headers(
            await _run(self.make(), _scope(peer="203.0.113.9", extra_headers=[("x-forwarded-proto", "https")]))
        )
        self.assertEqual(status, 200)

    async def test_request_id_from_client_is_accepted_only_if_well_formed(self):
        _, good = _status_and_headers(await _run(self.make(), _scope(extra_headers=[("x-request-id", "abcdef1234")])))
        self.assertEqual(good["x-request-id"], "abcdef1234")
        _, bad = _status_and_headers(await _run(self.make(), _scope(extra_headers=[("x-request-id", "evil\r\nset-cookie: x")])))
        self.assertNotIn("evil", bad["x-request-id"])
        self.assertRegex(bad["x-request-id"], r"^[0-9a-f]{32}$")

    async def test_docs_csp_relaxed_only_in_development(self):
        strict = self.make(expose_api_docs=False)
        status, headers = _status_and_headers(await _run(strict, _scope(path="/docs")))
        self.assertIn("content-security-policy", headers)
        relaxed = self.make(expose_api_docs=True)
        _, headers = _status_and_headers(await _run(relaxed, _scope(path="/docs")))
        self.assertNotIn("default-src 'none'", headers.get("content-security-policy", ""))

    def test_normalize_host(self):
        self.assertEqual(normalize_host("CFO.example.com:443"), "cfo.example.com")
        self.assertEqual(normalize_host("[::1]:8000"), "::1")


if __name__ == "__main__":
    unittest.main()
