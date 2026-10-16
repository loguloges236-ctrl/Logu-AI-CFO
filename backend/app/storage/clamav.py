"""ClamAV clamd client using the INSTREAM protocol (stdlib only).

Fail-closed contract:
- Only a reply of "stream: OK" yields CLEAN.
- "stream: <signature> FOUND" yields INFECTED.
- Anything else (connection error, timeout, size-limit error, malformed reply) raises ScannerError,
  which the upload pipeline records as FAILED. Nothing is ever treated as clean by default.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

CHUNK_SIZE = 64 * 1024
MAX_REPLY_BYTES = 4096
MAX_SIGNATURE_LENGTH = 255


class ScannerError(Exception):
    """The scan did not complete. Safe to log; contains no document content."""


@dataclass(frozen=True)
class ScanResult:
    status: str  # "CLEAN" or "INFECTED"
    signature: str | None = None


def parse_reply(raw: bytes) -> ScanResult:
    message = raw.decode("utf-8", errors="replace").strip()
    prefix = "stream: "
    if not message.startswith(prefix):
        raise ScannerError("Unexpected scanner reply.")
    body = message[len(prefix) :]
    if body == "OK":
        return ScanResult("CLEAN")
    if body.endswith(" FOUND"):
        signature = body[: -len(" FOUND")].strip()
        if not signature or len(signature) > MAX_SIGNATURE_LENGTH:
            raise ScannerError("Unexpected scanner signature.")
        return ScanResult("INFECTED", signature)
    raise ScannerError("Scanner returned an error.")


def _read_reply(sock: socket.socket) -> bytes:
    buffer = bytearray()
    while True:
        part = sock.recv(1024)
        if not part:
            break
        buffer.extend(part)
        if b"\x00" in part or len(buffer) > MAX_REPLY_BYTES:
            break
    if len(buffer) > MAX_REPLY_BYTES:
        raise ScannerError("Scanner reply too large.")
    return bytes(buffer).split(b"\x00", 1)[0]


class ClamAVScanner:
    def __init__(self, host: str, port: int, timeout_seconds: float) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds

    def scan(self, data: bytes) -> ScanResult:
        if not data:
            raise ScannerError("Nothing to scan.")
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise ScannerError("Scanner is unavailable.") from exc
        with sock:
            try:
                sock.sendall(b"zINSTREAM\x00")
                view = memoryview(data)
                for offset in range(0, len(data), CHUNK_SIZE):
                    chunk = view[offset : offset + CHUNK_SIZE]
                    sock.sendall(struct.pack("!I", len(chunk)))
                    sock.sendall(chunk)
                sock.sendall(struct.pack("!I", 0))
                reply = _read_reply(sock)
            except OSError as exc:
                raise ScannerError("Scanner connection failed.") from exc
        return parse_reply(reply)
