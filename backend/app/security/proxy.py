"""Client-address and scheme resolution behind a reverse proxy.

Rules:
- Forwarded headers are honoured ONLY when the immediate TCP peer is inside TRUSTED_PROXY_NETWORKS.
- X-Forwarded-For is walked right-to-left; trusted proxy hops are skipped and the first
  untrusted address is the client. Client-supplied leftmost entries are therefore ignored.
- An unparseable entry yields an unknown client rather than a guess.

Pure Python (stdlib only) so it can be unit-tested without the web framework.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from typing import Any

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address

CLIENT_IP_KEY = "logu.client_ip"
PEER_IP_KEY = "logu.peer_ip"
TRUSTED_SCHEME_KEY = "logu.trusted_forwarded_scheme"
UNKNOWN_CLIENT = "unknown"


def parse_networks(items: Iterable[str]) -> list[Network]:
    return [ipaddress.ip_network(item.strip(), strict=False) for item in items if item.strip()]


def _parse_address(value: str | None) -> Address | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(address: Address, trusted: Sequence[Network]) -> bool:
    return any(address in network for network in trusted)


def resolve_client(
    peer: str | None,
    forwarded_for: str | None,
    forwarded_proto: str | None,
    trusted: Sequence[Network],
) -> tuple[str | None, str | None]:
    """Return (client_ip, forwarded_scheme). forwarded_scheme is None unless trusted."""
    peer_address = _parse_address(peer)
    if peer_address is None or not _is_trusted(peer_address, trusted):
        return peer, None

    client: str | None = str(peer_address)
    chain = [item.strip() for item in (forwarded_for or "").split(",") if item.strip()]
    for item in reversed(chain):
        address = _parse_address(item)
        if address is None:
            return None, None
        client = str(address)
        if not _is_trusted(address, trusted):
            break

    scheme: str | None = None
    if forwarded_proto:
        candidate = forwarded_proto.split(",")[-1].strip().lower()
        if candidate in {"http", "https"}:
            scheme = candidate
    return client, scheme


def is_trusted_peer(peer: str | None, trusted: Sequence[Network]) -> bool:
    """True when the immediate TCP peer is a trusted reverse proxy (for example, Caddy)."""
    peer_address = _parse_address(peer)
    return peer_address is not None and _is_trusted(peer_address, trusted)


def _header_values(scope: dict[str, Any], name: bytes) -> str | None:
    values = [value.decode("latin-1") for key, value in scope.get("headers", []) if key == name]
    return ",".join(values) if values else None


def apply_proxy_resolution(scope: dict[str, Any], trusted: Sequence[Network]) -> dict[str, Any]:
    """Return a copy of an HTTP scope with the resolved client address and scheme."""
    client = scope.get("client")
    peer = client[0] if client else None
    client_ip, forwarded_scheme = resolve_client(
        peer,
        _header_values(scope, b"x-forwarded-for"),
        _header_values(scope, b"x-forwarded-proto"),
        trusted,
    )
    updated = dict(scope)
    updated[PEER_IP_KEY] = peer
    # Set only when a trusted proxy reported the scheme; never from an untrusted client.
    updated[TRUSTED_SCHEME_KEY] = forwarded_scheme
    updated[CLIENT_IP_KEY] = client_ip
    if forwarded_scheme is not None:
        updated["scheme"] = forwarded_scheme
    return updated
