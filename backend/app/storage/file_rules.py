"""Upload content rules (pure functions, no framework imports)."""

from __future__ import annotations

import re
from pathlib import PurePath
from urllib.parse import quote

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}
ALLOWED_CONTENT_TYPES = frozenset({*_SIGNATURES, "image/webp"})


def detect_content_type(content: bytes) -> str | None:
    """Identify the type from magic bytes. The declared Content-Type is never trusted on its own."""
    for content_type, signatures in _SIGNATURES.items():
        if any(content.startswith(signature) for signature in signatures):
            return content_type
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_display_name(filename: str | None) -> str:
    """Strip directory components, control characters and header-breaking characters."""
    name = PurePath((filename or "document").replace("\\", "/")).name.replace("\x00", "").strip()
    name = re.sub(r"[\r\n\"<>|]", "_", name)
    return (name or "document")[:255]


def content_disposition(filename: str) -> str:
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:100] or "document"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
