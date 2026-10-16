import tempfile
import unittest
import uuid
from pathlib import Path

from app.security.keys import KeyedDigester, is_storage_key, new_storage_key
from app.storage.file_rules import content_disposition, detect_content_type, safe_display_name
from app.storage.local import LocalFileStorage, StorageError, StorageNotFound


class KeyedDigestTests(unittest.TestCase):
    def test_digest_is_keyed(self):
        a = KeyedDigester(b"a" * 32).digest("session", "token")
        b = KeyedDigester(b"b" * 32).digest("session", "token")
        self.assertNotEqual(a, b)

    def test_digest_is_domain_separated(self):
        d = KeyedDigester(b"k" * 32)
        self.assertNotEqual(d.digest("session", "value"), d.digest("csrf", "value"))

    def test_short_key_is_refused(self):
        with self.assertRaises(ValueError):
            KeyedDigester(b"short")

    def test_storage_keys_are_opaque_and_validated(self):
        key = new_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        self.assertTrue(is_storage_key(key))
        self.assertFalse(is_storage_key("../etc/passwd"))
        self.assertFalse(is_storage_key("a/b/c"))


class FileRuleTests(unittest.TestCase):
    def test_magic_bytes_identify_type(self):
        self.assertEqual(detect_content_type(b"%PDF-1.7 ..."), "application/pdf")
        self.assertEqual(detect_content_type(b"\xff\xd8\xff\xe0"), "image/jpeg")
        self.assertEqual(detect_content_type(b"\x89PNG\r\n\x1a\nrest"), "image/png")
        self.assertEqual(detect_content_type(b"RIFF0000WEBPVP8 "), "image/webp")

    def test_mismatched_or_unknown_content_is_not_identified(self):
        self.assertIsNone(detect_content_type(b"<html><script>alert(1)</script>"))
        self.assertIsNone(detect_content_type(b"RIFF0000AVI LIST"))

    def test_display_name_strips_paths_and_header_breakers(self):
        self.assertEqual(safe_display_name("../../etc/passwd"), "passwd")
        self.assertEqual(safe_display_name("C:\\Users\\x\\bill.pdf"), "bill.pdf")
        self.assertEqual(safe_display_name('bad"\r\nname.pdf'), "bad___name.pdf")
        self.assertEqual(safe_display_name(None), "document")

    def test_content_disposition_is_attachment_and_encoded(self):
        header = content_disposition("பில் 1.pdf")
        self.assertTrue(header.startswith("attachment;"))
        self.assertIn("filename*=UTF-8''", header)
        self.assertNotIn("\n", header)


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = LocalFileStorage(Path(self._tmp.name) / "uploads")
        self.key = new_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_and_delete(self):
        self.storage.put(self.key, b"ciphertext-bytes")
        self.assertEqual(self.storage.read(self.key), b"ciphertext-bytes")
        self.storage.delete(self.key)
        with self.assertRaises(StorageNotFound):
            self.storage.read(self.key)
        self.storage.delete(self.key)  # idempotent

    def test_existing_blob_is_never_overwritten(self):
        self.storage.put(self.key, b"first")
        with self.assertRaises(StorageError):
            self.storage.put(self.key, b"second")
        self.assertEqual(self.storage.read(self.key), b"first")

    def test_invalid_keys_are_refused(self):
        with self.assertRaises(StorageError):
            self.storage.put("../escape", b"x")
        with self.assertRaises(StorageError):
            self.storage.read("../../etc/passwd")

    def test_blob_files_are_private(self):
        import os
        import stat

        if os.name != "posix":
            self.skipTest("POSIX permission bits only")
        self.storage.put(self.key, b"x")
        mode = stat.S_IMODE(os.stat(self.storage.root / self.key).st_mode)
        self.assertEqual(mode, 0o600)

    def test_iter_keys_lists_only_valid_blobs(self):
        self.storage.put(self.key, b"x")
        (self.storage.root / "stray.txt").write_bytes(b"not a blob")
        self.assertEqual(list(self.storage.iter_keys()), [self.key])


if __name__ == "__main__":
    unittest.main()
