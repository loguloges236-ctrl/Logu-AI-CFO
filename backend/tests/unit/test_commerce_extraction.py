"""Document-processing rules: provider contract, validation, confidence gating and totals."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.commerce import extraction as x


def good_bill() -> dict[str, object]:
    return {
        "party_id": "00000000-0000-0000-0000-000000000001",
        "reference_number": "INV-7781",
        "document_date": "2026-10-01",
        "grand_total": "118.00",
        "lines": [
            {"description": "Widget", "quantity": "2", "rate": "50.00", "tax_rate": "18", "discount_percent": "0"}
        ],
    }


class ProviderContractTests(unittest.TestCase):
    def test_default_provider_reports_unavailable_and_returns_no_data(self) -> None:
        result = x.UnavailableExtractionProvider().extract(b"%PDF", "application/pdf")
        self.assertFalse(result.available)
        self.assertEqual(result.fields, {})
        self.assertIsNone(result.classification)

    def test_provider_fields_split_into_values_and_confidence(self) -> None:
        result = x.ProviderResult(
            available=True,
            provider="test",
            fields={
                "reference_number": {"value": "A1", "confidence": 0.95},
                "grand_total": {"value": 118.0, "confidence": "0.9"},
                "due_date": {"value": "2026-11-01"},
                "notes": "not a dict",
            },
        )
        values, confidences = x.payload_from_provider(result)
        self.assertEqual(values["grand_total"], 118.0)
        self.assertEqual(confidences["reference_number"], Decimal("0.95"))
        self.assertEqual(confidences["due_date"], Decimal("0"))
        self.assertNotIn("notes", values)

    def test_float_from_provider_is_not_used_as_binary(self) -> None:
        self.assertEqual(x._to_decimal(0.1), Decimal("0.1"))


class ValidationTests(unittest.TestCase):
    def test_clean_bill_is_ready(self) -> None:
        outcome = x.validate_payload(
            "purchase_bill", good_bill(), {"reference_number": Decimal("0.99")}, Decimal("0.97")
        )
        self.assertEqual(outcome.status, "ready", outcome.issues)

    def test_missing_party_and_reference_need_review(self) -> None:
        payload = good_bill()
        payload.pop("party_id")
        payload["reference_number"] = ""
        outcome = x.validate_payload("purchase_bill", payload)
        codes = {(issue.field, issue.code) for issue in outcome.issues}
        self.assertIn(("party_id", "missing_field"), codes)
        self.assertIn(("reference_number", "missing_field"), codes)
        self.assertEqual(outcome.status, "needs_review")

    def test_total_mismatch_is_flagged_to_the_paisa(self) -> None:
        payload = good_bill()
        payload["grand_total"] = "118.01"
        outcome = x.validate_payload("purchase_bill", payload)
        self.assertIn("total_mismatch", {issue.code for issue in outcome.issues})

    def test_exact_total_passes_and_one_paisa_difference_needs_review(self) -> None:
        exact = x.validate_payload("purchase_bill", good_bill())
        self.assertNotIn("total_mismatch", {issue.code for issue in exact.issues})
        payload = good_bill()
        payload["grand_total"] = "117.99"
        outcome = x.validate_payload("purchase_bill", payload)
        self.assertIn("total_mismatch", {issue.code for issue in outcome.issues})

    def test_low_confidence_field_forces_review(self) -> None:
        outcome = x.validate_payload(
            "purchase_bill", good_bill(), {"grand_total": Decimal("0.55")}, Decimal("0.99")
        )
        self.assertEqual(outcome.status, "needs_review")
        self.assertIn(("grand_total", "low_confidence"), {(i.field, i.code) for i in outcome.issues})

    def test_ambiguous_classification_forces_review(self) -> None:
        outcome = x.validate_payload("purchase_bill", good_bill(), {}, Decimal("0.42"))
        self.assertIn("ambiguous_classification", {i.code for i in outcome.issues})
        self.assertEqual(outcome.status, "needs_review")

    def test_bad_values_are_reported_not_raised(self) -> None:
        payload = good_bill()
        payload["document_date"] = "10/01/2026"
        payload["lines"] = [{"description": "x", "quantity": "-1", "rate": "abc"}, "junk"]
        payload["grand_total"] = "-5"
        outcome = x.validate_payload("purchase_bill", payload)
        codes = {issue.code for issue in outcome.issues}
        self.assertTrue({"invalid_date", "invalid_quantity", "invalid_amount", "invalid_line"} <= codes)

    def test_unknown_type_and_line_without_identity(self) -> None:
        outcome = x.validate_payload("mystery", {"lines": [{"quantity": "1", "rate": "1"}]})
        codes = {issue.code for issue in outcome.issues}
        self.assertIn("unknown_type", codes)
        self.assertIn("line_unidentified", codes)

    def test_draft_targets_are_defined(self) -> None:
        self.assertTrue(x.DRAFT_TARGETS <= set(x.DOCUMENT_TYPES))


if __name__ == "__main__":
    unittest.main()
