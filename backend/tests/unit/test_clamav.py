"""Exercises the clamd INSTREAM client against a local fake clamd speaking the same wire protocol."""

import os
import socket
import struct
import threading
import unittest

from app.storage.clamav import ClamAVScanner, ScannerError, parse_reply


class FakeClamd:
    """Accepts one connection, reads an INSTREAM request, replies with a canned message."""

    def __init__(self, reply: bytes):
        self._reply = reply
        self.received = bytearray()
        self.command = b""
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._listener.close()
        self._thread.join(timeout=5)

    def _read_exact(self, conn, count):
        buffer = bytearray()
        while len(buffer) < count:
            part = conn.recv(count - len(buffer))
            if not part:
                raise ConnectionError("client closed early")
            buffer.extend(part)
        return bytes(buffer)

    def _serve(self):
        conn, _ = self._listener.accept()
        with conn:
            command = bytearray()
            while not command.endswith(b"\x00"):
                command.extend(self._read_exact(conn, 1))
            self.command = bytes(command)
            while True:
                (length,) = struct.unpack("!I", self._read_exact(conn, 4))
                if length == 0:
                    break
                self.received.extend(self._read_exact(conn, length))
            conn.sendall(self._reply)


class ParseReplyTests(unittest.TestCase):
    def test_ok(self):
        result = parse_reply(b"stream: OK")
        self.assertEqual(result.status, "CLEAN")

    def test_found(self):
        result = parse_reply(b"stream: Eicar-Test-Signature FOUND")
        self.assertEqual(result.status, "INFECTED")
        self.assertEqual(result.signature, "Eicar-Test-Signature")

    def test_error_and_garbage_fail_closed(self):
        for reply in (
            b"stream: INSTREAM size limit exceeded. ERROR",
            b"stream: lstat() failed ERROR",
            b"",
            b"OK",
            b"stream: ",
        ):
            with self.assertRaises(ScannerError, msg=reply):
                parse_reply(reply)

    def test_oversized_signature_is_rejected(self):
        with self.assertRaises(ScannerError):
            parse_reply(b"stream: " + b"A" * 300 + b" FOUND")


class ClamAVClientTests(unittest.TestCase):
    def test_clean_document_is_streamed_in_chunks(self):
        payload = os.urandom(200 * 1024)  # forces several 64 KiB chunks
        with FakeClamd(b"stream: OK\x00") as clamd:
            result = ClamAVScanner("127.0.0.1", clamd.port, 5).scan(payload)
        self.assertEqual(result.status, "CLEAN")
        self.assertEqual(clamd.command, b"zINSTREAM\x00")
        self.assertEqual(bytes(clamd.received), payload)

    def test_infected_document_reports_signature(self):
        with FakeClamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\x00") as clamd:
            result = ClamAVScanner("127.0.0.1", clamd.port, 5).scan(b"X5O!P%@AP")
        self.assertEqual(result.status, "INFECTED")
        self.assertEqual(result.signature, "Win.Test.EICAR_HDB-1")

    def test_scanner_error_reply_raises(self):
        with FakeClamd(b"stream: INSTREAM size limit exceeded. ERROR\x00") as clamd:
            with self.assertRaises(ScannerError):
                ClamAVScanner("127.0.0.1", clamd.port, 5).scan(b"data")

    def test_unreachable_scanner_fails_closed(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # nothing listens on this port now
        with self.assertRaises(ScannerError):
            ClamAVScanner("127.0.0.1", port, 2).scan(b"data")

    def test_empty_input_is_never_reported_clean(self):
        with self.assertRaises(ScannerError):
            ClamAVScanner("127.0.0.1", 9, 1).scan(b"")


if __name__ == "__main__":
    unittest.main()
