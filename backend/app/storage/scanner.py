"""Malware-scanner interface and factory.

There is no development bypass. Every upload must be scanned by a real scanner service (ClamAV).
Tests inject a fake scanner through FastAPI dependency overrides; that fake lives in tests/ only.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import get_settings
from app.storage.clamav import ClamAVScanner, ScanResult


class MalwareScanner(Protocol):
    def scan(self, data: bytes) -> ScanResult:
        """Return CLEAN or INFECTED. Raise ScannerError if a verdict cannot be obtained."""


def build_scanner() -> MalwareScanner:
    settings = get_settings()
    return ClamAVScanner(
        settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds
    )
