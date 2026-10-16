"""Request validation for the accounting API (pydantic only, runnable without the web stack)."""

import unittest
import uuid

from pydantic import ValidationError

from app.schemas.accounting import AccountCreate, EntryIn, LineIn, PeriodIn

ACCOUNT = str(uuid.uuid4())


class AccountSchemaTests(unittest.TestCase):
    def test_valid_account_is_accepted(self):
        model = AccountCreate(code="1150", name="Cash drawer", category="asset", subtype="cash")
        self.assertEqual(model.code, "1150")

    def test_code_must_be_digits(self):
        with self.assertRaises(ValidationError):
            AccountCreate(code="11A0", name="x", category="asset", subtype="cash")

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValidationError):
            AccountCreate(code="1150", name="x", category="revenue", subtype="cash")

    def test_extra_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            AccountCreate(code="1150", name="x", category="asset", subtype="cash", balance="999")


class EntrySchemaTests(unittest.TestCase):
    def test_money_must_be_a_string_not_a_json_number(self):
        with self.assertRaises(ValidationError):
            LineIn(account_id=ACCOUNT, debit=10.5, credit="0")

    def test_money_string_is_accepted_and_kept_exact(self):
        line = LineIn(account_id=ACCOUNT, debit="10.50", credit="0")
        self.assertEqual(line.debit, "10.50")

    def test_tax_rate_pattern_is_enforced(self):
        LineIn(account_id=ACCOUNT, tax_rate="18.0000")
        with self.assertRaises(ValidationError):
            LineIn(account_id=ACCOUNT, tax_rate="18.12345")

    def test_entry_line_count_is_capped_at_schema_level(self):
        lines = [{"account_id": ACCOUNT, "debit": "1", "credit": "0"}] * 201
        with self.assertRaises(ValidationError):
            EntryIn(transaction_date="2026-10-01", description="x", lines=lines)

    def test_entry_requires_description(self):
        with self.assertRaises(ValidationError):
            EntryIn(transaction_date="2026-10-01", description="", lines=[])


class PeriodSchemaTests(unittest.TestCase):
    def test_period_dates_must_be_iso_dates(self):
        PeriodIn(name="Oct 2026", start_date="2026-10-01", end_date="2026-10-31")
        with self.assertRaises(ValidationError):
            PeriodIn(name="Oct 2026", start_date="01/10/2026", end_date="2026-10-31")


if __name__ == "__main__":
    unittest.main()
