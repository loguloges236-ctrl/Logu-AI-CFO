"""Private encrypted-blob storage on a local or mounted volume.

- Root directory must lie outside the source tree and any web-served directory (validated at startup).
- Keys are opaque hex paths (see app.security.keys.is_storage_key); user-supplied names never reach the path.
- Files are written with mode 0600 via O_EXCL on a temporary name, then linked to the final name so an
  existing blob is never overwritten.
- Symbolic links are refused on read and delete.
- Bytes stored here are ciphertext (Fernet). Plaintext documents are never written to disk.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

from app.security.keys import is_storage_key

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class StorageError(Exception):
    """Storage operation failed or the key is invalid. Message contains no paths or content."""


class StorageNotFound(StorageError):
    pass


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        if not is_storage_key(key):
            raise StorageError("Invalid storage key.")
        candidate = (self.root / key).resolve()
        if self.root not in candidate.parents:
            raise StorageError("Invalid storage key.")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(f".{secrets.token_hex(12)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, destination)  # fails with FileExistsError if the key is already used
        except FileExistsError as exc:
            raise StorageError("Storage key collision.") from exc
        except OSError as exc:
            raise StorageError("Storage write failed.") from exc
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file() or path.is_symlink():
            raise StorageNotFound("Stored file is unavailable.")
        try:
            fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                return handle.read()
        except OSError as exc:
            raise StorageError("Storage read failed.") from exc

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            if path.is_symlink():
                raise StorageError("Refusing to delete a symbolic link.")
            path.unlink()
        except FileNotFoundError:
            return

    def exists(self, key: str) -> bool:
        path = self._path(key)
        return path.is_file() and not path.is_symlink()

    def modified_at(self, key: str) -> float:
        return self._path(key).stat().st_mtime

    def iter_keys(self) -> Iterator[str]:
        """Yield every valid storage key currently on disk (used by reconciliation)."""
        for path in self.root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if is_storage_key(relative):
                yield relative
