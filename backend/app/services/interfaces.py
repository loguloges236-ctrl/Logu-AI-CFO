"""Provider contracts for later phases.

Only the contracts exist. No implementation is shipped in Phase 1 except the explicit
"disabled" providers below, which report themselves as unavailable so callers fail closed.

Rules that every future implementation must respect:
- AIProvider returns structured SUGGESTIONS only. It never writes to the database and never
  becomes an accounting source of truth.
- Bank adapters must be read-only: no transfer, payment, beneficiary or account-modification capability.
- Voice enrollment/verification is NOT implemented. Speech-to-text is never biometric authentication.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class AIProvider(ABC):
    @abstractmethod
    def classify_document(self, content: bytes, content_type: str) -> dict[str, Any]: ...

    @abstractmethod
    def extract_document(self, content: bytes, content_type: str) -> dict[str, Any]: ...

    @abstractmethod
    def answer_financial_question(self, question: str, context: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float
    engine: str


class OCRProvider(ABC):
    """Phase 2 document intelligence. No OCR engine is integrated in Phase 1."""

    @abstractmethod
    def extract_text(self, content: bytes, content_type: str) -> OCRResult: ...


class NotificationProvider(ABC):
    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def send(self, *, channel: str, recipient: str, title: str, body: str) -> None: ...


class DisabledNotificationProvider(NotificationProvider):
    @property
    def available(self) -> bool:
        return False

    def send(self, *, channel: str, recipient: str, title: str, body: str) -> None:
        raise RuntimeError("No notification provider is configured.")


@dataclass(frozen=True)
class SpeakerVerificationResult:
    matched: bool
    confidence: float
    provider_reference: str | None = None


class SpeakerVerificationProvider(ABC):
    @abstractmethod
    def enroll(self, audio: bytes) -> str: ...

    @abstractmethod
    def verify(self, enrollment_reference: str, audio: bytes) -> SpeakerVerificationResult: ...


class AntiSpoofingProvider(ABC):
    @abstractmethod
    def assess(self, audio: bytes) -> bool:
        """Return True only when the sample passes anti-spoofing checks."""
