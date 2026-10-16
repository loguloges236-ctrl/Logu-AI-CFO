"""Document-processing contract: provider interface, deterministic validation and confidence gating.

Pipeline (orchestrated by app/services/commerce/documents.py):

    UPLOAD -> SECURITY SCAN (Phase 1) -> PROVIDER (OCR / document AI) -> CLASSIFICATION
           -> EXTRACTION -> VALIDATION (this module) -> REVIEW if needed -> APPROVAL -> DRAFT

Rules that do not depend on any provider:
- Provider output is a SUGGESTION. It is stored as raw evidence and never used directly.
- Every extracted field carries a confidence. Anything below the threshold, any failed check, or a
  missing provider makes the job "needs_review". Only a job with no issues can be "ready".
- The owner can correct any field. Corrections are re-validated from scratch.
- Approval creates a DRAFT business document. Stock and accounting change only on a later, explicit post.
- No provider is bundled. `UnavailableExtractionProvider` returns no data, so the job waits for manual entry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.commerce.domain import line_amounts, parse_iso_date, q2

DOCUMENT_TYPES = (
    "purchase_bill",
    "purchase_order",
    "quotation",
    "sales_order",
    "sales_invoice",
    "goods_receipt",
    "receipt",
    "customer_document",
    "bank_statement",
    "salary",
    "emi",
    "interest",
    "debt",
    "recurring_payment",
    "other",
)

# Types the approval step can turn into a DRAFT business document.
DRAFT_TARGETS = frozenset(
    {"purchase_bill", "purchase_order", "quotation", "sales_order", "emi", "interest", "salary", "debt", "recurring_payment"}
)

# Finance types whose amount must be strictly positive.
POSITIVE_AMOUNT_TYPES = frozenset({"emi", "interest", "salary", "customer_document", "debt", "recurring_payment"})

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "purchase_bill": ("party_id", "reference_number", "document_date", "lines"),
    "purchase_order": ("party_id", "document_date", "lines"),
    "quotation": ("party_id", "document_date", "lines"),
    "sales_order": ("party_id", "document_date", "lines"),
    "sales_invoice": ("party_name", "reference_number", "document_date", "lines"),
    "goods_receipt": ("reference_number", "document_date", "lines"),
    "receipt": ("document_date", "grand_total"),
    "customer_document": ("party_name", "document_date", "grand_total"),
    "bank_statement": ("reference_number", "lines"),
    "salary": ("party_name", "document_date", "grand_total"),
    "emi": ("party_name", "reference_number", "document_date", "due_date", "grand_total"),
    "interest": ("party_name", "document_date", "due_date", "grand_total"),
    "debt": ("party_name", "reference_number", "document_date", "grand_total", "interest_rate", "tenure_months"),
    "recurring_payment": ("party_name", "document_date", "due_date", "grand_total"),
    "other": (),
}

# Finance documents carry a party name, not a catalogue party, so they do not need party_id.
FINANCE_NO_PARTY_ID = frozenset({"emi", "interest", "salary", "debt", "recurring_payment"})

CONFIDENCE_THRESHOLD = Decimal("0.80")
# Exact: a one-paisa difference is a review item, because vendor rounding is not ours to decide.
MONEY_TOLERANCE = Decimal("0")


@dataclass(frozen=True)
class ProviderResult:
    """What a provider returned. `available` is False when no provider is configured."""

    available: bool
    provider: str
    classification: str | None = None
    classification_confidence: Decimal | None = None
    # field name -> {"value": <JSON value>, "confidence": Decimal between 0 and 1}
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class DocumentExtractionProvider(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def extract(self, content: bytes, content_type: str) -> ProviderResult: ...


class UnavailableExtractionProvider(DocumentExtractionProvider):
    """Default. Reports that no OCR or document AI is configured; it never invents data."""

    name = "unavailable"

    def extract(self, content: bytes, content_type: str) -> ProviderResult:
        return ProviderResult(available=False, provider=self.name)


@dataclass(frozen=True)
class Issue:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ValidationOutcome:
    status: str  # "ready" | "needs_review"
    issues: list[Issue]
    field_confidence: dict[str, str]
    classification: str
    classification_confidence: str | None


def _to_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Through str() so a binary float never becomes an exact-looking money value.
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str) and value.strip():
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _confidence(value: object) -> Decimal | None:
    number = _to_decimal(value)
    if number is None or number < 0 or number > 1:
        return None
    return number


def validate_payload(
    document_type: str,
    payload: dict[str, Any],
    field_confidence: dict[str, Decimal] | None = None,
    classification_confidence: Decimal | None = None,
) -> ValidationOutcome:
    """Deterministic checks. Returns an outcome; never raises for bad data."""
    issues: list[Issue] = []
    confidences = field_confidence or {}

    if document_type not in DOCUMENT_TYPES:
        issues.append(Issue("document_type", "unknown_type", "The document type is not recognised"))
    for name in REQUIRED_FIELDS.get(document_type, ()):
        value = payload.get(name)
        if value in (None, "", [], {}):
            issues.append(Issue(name, "missing_field", f"{name.replace('_', ' ').capitalize()} is required"))

    for date_field in ("document_date", "due_date"):
        if payload.get(date_field) not in (None, ""):
            try:
                parse_iso_date(payload[date_field], date_field)
            except Exception:
                issues.append(Issue(date_field, "invalid_date", "Date must be YYYY-MM-DD"))

    lines = payload.get("lines")
    computed_total = Decimal("0")
    if lines not in (None, ""):
        if not isinstance(lines, list):
            issues.append(Issue("lines", "invalid_lines", "Lines must be a list"))
        else:
            for index, line in enumerate(lines, start=1):
                if not isinstance(line, dict):
                    issues.append(Issue(f"lines[{index}]", "invalid_line", "Line is not valid"))
                    continue
                quantity = _to_decimal(line.get("quantity"))
                rate = _to_decimal(line.get("rate"))
                discount = _to_decimal(line.get("discount_percent") or "0")
                tax = _to_decimal(line.get("tax_rate") or "0")
                if quantity is None or quantity <= 0:
                    issues.append(Issue(f"lines[{index}].quantity", "invalid_quantity", "Quantity must be above zero"))
                if rate is None or rate < 0:
                    issues.append(Issue(f"lines[{index}].rate", "invalid_amount", "Rate must not be negative"))
                if discount is None or not (0 <= discount <= 100) or tax is None or not (0 <= tax <= 100):
                    issues.append(Issue(f"lines[{index}]", "invalid_percent", "Discount and tax must be 0-100"))
                elif quantity and rate is not None and quantity > 0 and rate >= 0:
                    computed_total += line_amounts(quantity, rate, discount, tax).total
                if not line.get("description") and not line.get("product_id"):
                    issues.append(Issue(f"lines[{index}]", "line_unidentified", "Line needs a product or a description"))

    grand = _to_decimal(payload.get("grand_total"))
    if payload.get("grand_total") not in (None, "") and (grand is None or grand < 0):
        issues.append(Issue("grand_total", "invalid_amount", "Total must be a non-negative amount"))
    elif grand is not None and isinstance(lines, list) and lines:
        # Compare the stated total with the line arithmetic to the paisa. Mismatch is never auto-corrected.
        if abs(q2(grand) - computed_total) > MONEY_TOLERANCE:
            issues.append(Issue("grand_total", "total_mismatch", "The total does not match the line amounts"))

    if document_type in POSITIVE_AMOUNT_TYPES and grand is not None and grand <= 0:
        issues.append(Issue("grand_total", "invalid_amount", "Amount must be greater than zero"))

    if document_type in DRAFT_TARGETS and document_type not in FINANCE_NO_PARTY_ID and not payload.get("party_id"):
        issues.append(Issue("party_id", "party_not_selected", "Select the supplier or customer"))

    low_fields: list[str] = []
    for name, value in confidences.items():
        if value < CONFIDENCE_THRESHOLD:
            low_fields.append(name)
    for name in sorted(low_fields):
        issues.append(Issue(name, "low_confidence", "Extraction is not certain. Please check this value."))
    if classification_confidence is not None and classification_confidence < CONFIDENCE_THRESHOLD:
        issues.append(Issue("document_type", "ambiguous_classification", "The document type is uncertain"))

    status = "ready" if not issues else "needs_review"
    return ValidationOutcome(
        status=status,
        issues=issues,
        field_confidence={name: str(value) for name, value in confidences.items()},
        classification=document_type if document_type in DOCUMENT_TYPES else "other",
        classification_confidence=None if classification_confidence is None else str(classification_confidence),
    )


@dataclass(frozen=True)
class Fingerprint:
    """What an earlier job recorded about its file and its business reference."""

    job_id: str
    sha256: str | None
    party_key: str | None
    reference: str | None


def duplicate_issues(
    document_type: str,
    payload: dict[str, Any],
    sha256: str | None,
    party_key: str | None,
    earlier: list[Fingerprint],
) -> list[Issue]:
    """Duplicate detection. The same file, or the same supplier/customer with the same reference, is flagged for
    review. It is never dropped silently: the owner can acknowledge it and continue."""
    issues: list[Issue] = []
    reference = str(payload.get("reference_number") or "").strip().lower() or None
    for item in earlier:
        if sha256 and item.sha256 == sha256:
            issues.append(Issue("document", "duplicate_file", f"This exact file was already processed (job {item.job_id})"))
            break
    if reference and party_key:
        for item in earlier:
            if item.reference == reference and item.party_key == party_key:
                issues.append(
                    Issue("reference_number", "duplicate_reference", f"Same party and reference already recorded (job {item.job_id})")
                )
                break
    return issues


ACKNOWLEDGEABLE_DUPLICATE_CODES = frozenset({"duplicate_file", "duplicate_reference"})


def payload_from_provider(result: ProviderResult) -> tuple[dict[str, Any], dict[str, Decimal]]:
    """Split provider output into plain values and per-field confidences. Invalid confidences are dropped."""
    values: dict[str, Any] = {}
    confidences: dict[str, Decimal] = {}
    for name, entry in result.fields.items():
        if not isinstance(entry, dict):
            continue
        values[name] = entry.get("value")
        confidence = _confidence(entry.get("confidence"))
        # A value without a usable confidence is treated as uncertain (zero), so it needs review.
        confidences[name] = confidence if confidence is not None else Decimal("0")
    return values, confidences
