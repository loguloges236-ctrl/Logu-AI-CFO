import unittest

from app.security.proxy import (
    CLIENT_IP_KEY,
    PEER_IP_KEY,
    apply_proxy_resolution,
    parse_networks,
    resolve_client,
)

TRUSTED = parse_networks(["172.28.10.10/32"])


class ResolveClientTests(unittest.TestCase):
    def test_untrusted_peer_headers_are_ignored(self):
        # A direct client cannot spoof its address or scheme.
        ip, scheme = resolve_client("203.0.113.9", "198.51.100.7", "http", TRUSTED)
        self.assertEqual(ip, "203.0.113.9")
        self.assertIsNone(scheme)

    def test_trusted_proxy_client_is_rightmost_untrusted_entry(self):
        ip, scheme = resolve_client("172.28.10.10", "198.51.100.7", "https", TRUSTED)
        self.assertEqual(ip, "198.51.100.7")
        self.assertEqual(scheme, "https")

    def test_spoofed_leftmost_entry_is_not_used(self):
        # Client sends "X-Forwarded-For: 1.2.3.4"; Caddy appends the real address on the right.
        ip, _ = resolve_client("172.28.10.10", "1.2.3.4, 198.51.100.7", None, TRUSTED)
        self.assertEqual(ip, "198.51.100.7")

    def test_chain_through_another_trusted_hop_is_walked(self):
        trusted = parse_networks(["172.28.10.0/24"])
        ip, _ = resolve_client("172.28.10.10", "198.51.100.7, 172.28.10.11", None, trusted)
        self.assertEqual(ip, "198.51.100.7")

    def test_unparseable_proxy_appended_entry_yields_unknown_client(self):
        ip, _ = resolve_client("172.28.10.10", "198.51.100.7, not-an-ip", None, TRUSTED)
        self.assertIsNone(ip)

    def test_unparseable_client_supplied_leftmost_entry_is_ignored(self):
        ip, _ = resolve_client("172.28.10.10", "not-an-ip, 198.51.100.7", None, TRUSTED)
        self.assertEqual(ip, "198.51.100.7")

    def test_missing_forwarded_for_from_trusted_proxy_uses_peer(self):
        ip, scheme = resolve_client("172.28.10.10", None, None, TRUSTED)
        self.assertEqual(ip, "172.28.10.10")
        self.assertIsNone(scheme)

    def test_invalid_forwarded_proto_is_ignored(self):
        _, scheme = resolve_client("172.28.10.10", "198.51.100.7", "javascript", TRUSTED)
        self.assertIsNone(scheme)

    def test_ipv6_clients_are_supported(self):
        ip, _ = resolve_client("172.28.10.10", "2001:db8::1", None, TRUSTED)
        self.assertEqual(ip, "2001:db8::1")


class ApplyProxyResolutionTests(unittest.TestCase):
    def scope(self, peer, headers):
        return {
            "type": "http",
            "client": (peer, 50000),
            "scheme": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers],
        }

    def test_trusted_forwarded_proto_updates_scheme(self):
        result = apply_proxy_resolution(
            self.scope("172.28.10.10", [("x-forwarded-for", "198.51.100.7"), ("x-forwarded-proto", "https")]),
            TRUSTED,
        )
        self.assertEqual(result["scheme"], "https")
        self.assertEqual(result[CLIENT_IP_KEY], "198.51.100.7")
        self.assertEqual(result[PEER_IP_KEY], "172.28.10.10")

    def test_untrusted_forwarded_proto_does_not_change_scheme(self):
        result = apply_proxy_resolution(
            self.scope("203.0.113.9", [("x-forwarded-proto", "https")]), TRUSTED
        )
        self.assertEqual(result["scheme"], "http")

    def test_original_scope_is_not_mutated(self):
        original = self.scope("172.28.10.10", [("x-forwarded-proto", "https")])
        apply_proxy_resolution(original, TRUSTED)
        self.assertEqual(original["scheme"], "http")
        self.assertNotIn(CLIENT_IP_KEY, original)


if __name__ == "__main__":
    unittest.main()
