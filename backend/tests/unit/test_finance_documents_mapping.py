"""Unit tests (no database): debt and recurring-payment document mapping, and the extraction rules they depend on."""

import unittest
from datetime import date
from decimal import Decimal

from app.commerce import extraction as ex
from app.finance.approval import draft_financial_record
from app.finance.domain import FinanceRuleViolation


def _conf(payload):
    return {k: Decimal("0.95") for k in payload}


class DebtDocumentTests(unittest.TestCase):
    PAYLOAD = {
        "party_name": "HDFC Bank", "reference_number": "LN-77", "document_date": "2026-10-01",
        "grand_total": "500000.00", "interest_rate": "10.5", "tenure_months": "24",
    }

    def test_debt_document_becomes_a_debt_draft_with_schedule_anchor(self):
        result = draft_financial_record("debt", dict(self.PAYLOAD), _conf(self.PAYLOAD))
        self.assertEqual(result.status, "ready")
        debt = result.debt
        self.assertEqual((debt.principal, debt.annual_rate, debt.tenure_months), (Decimal("500000.00"), Decimal("10.5"), 24))
        self.assertEqual(debt.start_date, date(2026, 10, 1))
        self.assertEqual(debt.first_due_date, date(2026, 11, 1))
        self.assertEqual(debt.interest_type, "reducing")
        self.assertIsNone(result.obligation)

    def test_invalid_tenure_or_interest_type_goes_to_review(self):
        bad_tenure = dict(self.PAYLOAD, tenure_months="0")
        self.assertEqual(draft_financial_record("debt", bad_tenure, _conf(bad_tenure)).status, "needs_review")
        bad_type = dict(self.PAYLOAD, interest_type="compound")
        result = draft_financial_record("debt", bad_type, _conf(bad_type))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.issues[0]["code"], "invalid_interest_type")

    def test_low_confidence_debt_is_not_created(self):
        conf = dict(_conf(self.PAYLOAD), grand_total=Decimal("0.5"))
        result = draft_financial_record("debt", dict(self.PAYLOAD), conf)
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.debt)


class RecurringPaymentTests(unittest.TestCase):
    def test_rent_is_a_critical_monthly_obligation(self):
        payload = {"party_name": "Landlord", "document_date": "2026-10-01", "due_date": "2026-10-05",
                   "grand_total": "25000.00", "recurring_kind": "rent", "frequency": "monthly"}
        result = draft_financial_record("recurring_payment", payload, _conf(payload))
        self.assertEqual(result.status, "ready")
        ob = result.obligation
        self.assertEqual((ob.kind, ob.frequency, ob.is_critical), ("rent", "monthly", True))
        self.assertEqual(ob.first_due_date, date(2026, 10, 5))

    def test_unknown_kind_or_frequency_goes_to_review(self):
        payload = {"party_name": "X", "document_date": "2026-10-01", "due_date": "2026-10-05",
                   "grand_total": "10.00", "recurring_kind": "lottery"}
        self.assertEqual(draft_financial_record("recurring_payment", payload, _conf(payload)).issues[0]["code"], "invalid_kind")
        payload = dict(payload, recurring_kind="rent", frequency="hourly")
        self.assertEqual(draft_financial_record("recurring_payment", payload, _conf(payload)).issues[0]["code"], "invalid_frequency")

    def test_non_finance_document_is_refused(self):
        with self.assertRaises(FinanceRuleViolation):
            draft_financial_record("purchase_bill", {}, {})


class ExtractionRuleTests(unittest.TestCase):
    def test_new_types_are_known_draft_targets_and_need_no_party_id(self):
        for kind in ("debt", "recurring_payment"):
            self.assertIn(kind, ex.DOCUMENT_TYPES)
            self.assertIn(kind, ex.DRAFT_TARGETS)
            self.assertIn(kind, ex.FINANCE_NO_PARTY_ID)
            self.assertIn(kind, ex.POSITIVE_AMOUNT_TYPES)

    def test_debt_document_without_rate_or_tenure_fails_validation(self):
        outcome = ex.validate_payload("debt", {"party_name": "Bank", "reference_number": "L1",
                                               "document_date": "2026-10-01", "grand_total": "1000.00"}, None, None)
        self.assertEqual(outcome.status, "needs_review")
        fields = {i.field for i in outcome.issues}
        self.assertTrue({"interest_rate", "tenure_months"} <= fields)


if __name__ == "__main__":
    unittest.main()
