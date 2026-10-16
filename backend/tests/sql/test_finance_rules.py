"""Finance, bank and notification database rules, run against a disposable PostgreSQL database.

Skipped unless COMMERCE_SQL_TEST_ADMIN_URI points to a PostgreSQL server (the test creates and drops its own
database). Uses the same statement-loading approach as tests/sql/test_commerce_rules.py.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import uuid
from pathlib import Path

from tests.sql.test_commerce_rules import ADMIN_URI, run_psql, with_database

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "20261008_0004_finance_bank.py"

STUBS = """
CREATE TABLE accounts (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE journal_entries (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE document_processing_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classification varchar(30),
    CONSTRAINT ck_document_jobs_classification CHECK (classification IS NULL OR classification IN ('purchase_bill'))
);
"""


def load_statements() -> tuple[str, ...]:
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("finance_migration", MIGRATION)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.STATEMENTS
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved


@unittest.skipUnless(ADMIN_URI, "COMMERCE_SQL_TEST_ADMIN_URI is not set")
class FinanceDatabaseRuleTests(unittest.TestCase):
    def setUp(self):
        # A fresh database per test: the rule tests use fixed identifiers and must not see each other's rows.
        self.db_name = f"logu_fin_{uuid.uuid4().hex[:10]}"
        create = run_psql(ADMIN_URI, f'CREATE DATABASE "{self.db_name}";')
        assert create.returncode == 0, create.stderr
        self.uri = with_database(ADMIN_URI, self.db_name)
        setup = run_psql(self.uri, STUBS + "\n".join(s + ";" for s in load_statements()))
        assert setup.returncode == 0, setup.stderr

    def tearDown(self):
        run_psql(ADMIN_URI, f'DROP DATABASE IF EXISTS "{self.db_name}";')

    def sql(self, statements: str):
        return run_psql(self.uri, statements)

    def ok(self, statements: str):
        result = self.sql(statements)
        self.assertEqual(result.returncode, 0, result.stderr)

    def refused(self, statements: str, fragment: str = ""):
        result = self.sql(statements)
        self.assertNotEqual(result.returncode, 0, "statement should have been refused")
        if fragment:
            self.assertIn(fragment, result.stderr)

    OBLIGATION = (
        "INSERT INTO payment_obligations (id, kind, title, amount, frequency, first_due_date) "
        "VALUES ('11111111-1111-4111-8111-111111111111', 'rent', 'Office rent', 25000.00, 'monthly', '2026-10-05');"
    )

    def test_obligation_and_partial_payment_are_accepted(self):
        self.ok(self.OBLIGATION + "INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on) "
                "VALUES ('11111111-1111-4111-8111-111111111111', '2026-10-05', 10000.00, '2026-10-06');")

    def test_payments_cannot_exceed_the_amount_due_for_a_period(self):
        self.refused(
            self.OBLIGATION + "INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on) "
            "VALUES ('11111111-1111-4111-8111-111111111111', '2026-10-05', 30000.00, '2026-10-06');",
            "would exceed",
        )

    def test_reversal_must_stay_within_payments_made(self):
        self.refused(
            self.OBLIGATION + "INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on) "
            "VALUES ('11111111-1111-4111-8111-111111111111', '2026-11-05', -1.00, '2026-11-06');",
            "cannot exceed",
        )

    def test_payment_records_are_append_only(self):
        self.refused(
            self.OBLIGATION
            + "INSERT INTO payment_records (id, obligation_id, period_due_date, amount, paid_on) "
            "VALUES ('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111', '2026-12-05', 100.00, '2026-12-05');"
            + "UPDATE payment_records SET amount = 200.00 WHERE id = '22222222-2222-4222-8222-222222222222';",
            "append-only",
        )
        self.refused(
            "DELETE FROM payment_records WHERE id = '22222222-2222-4222-8222-222222222222';",
            "append-only",
        )

    def test_reversal_is_a_negative_row_that_references_the_original(self):
        self.ok(
            self.OBLIGATION
            + "INSERT INTO payment_records (id, obligation_id, period_due_date, amount, paid_on) "
            "VALUES ('33333333-3333-4333-8333-333333333333', '11111111-1111-4111-8111-111111111111', '2027-01-05', 500.00, '2027-01-05');"
            + "INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on, reverses_record_id) "
            "VALUES ('11111111-1111-4111-8111-111111111111', '2027-01-05', -500.00, '2027-01-06', '33333333-3333-4333-8333-333333333333');"
        )

    def test_cancelled_obligation_refuses_payment(self):
        self.refused(
            "INSERT INTO payment_obligations (id, kind, title, amount, frequency, first_due_date, status) "
            "VALUES ('44444444-4444-4444-8444-444444444444', 'other', 'Old', 10.00, 'once', '2026-01-01', 'cancelled');"
            "INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on) "
            "VALUES ('44444444-4444-4444-8444-444444444444', '2026-01-01', 5.00, '2026-01-01');",
            "not open",
        )

    def test_unknown_obligation_kind_refused(self):
        self.refused("INSERT INTO payment_obligations (kind, title, amount, frequency, first_due_date) "
                     "VALUES ('crypto', 'x', 1.00, 'once', '2026-01-01');")

    def test_debt_schedule_must_split_and_roll_forward(self):
        debt = (
            "INSERT INTO debts (id, name, lender, principal, annual_rate, interest_type, emi, tenure_months, "
            "start_date, first_due_date, end_date) VALUES ('55555555-5555-4555-8555-555555555555', 'Car', 'Bank', "
            "100000.00, 12.0000, 'reducing', 8884.88, 12, '2026-01-01', '2026-02-01', '2027-01-01');"
        )
        self.ok(debt + "INSERT INTO debt_schedule_items (debt_id, installment_no, due_date, opening_balance, interest_amount, "
                "principal_amount, emi_amount, closing_balance) VALUES ('55555555-5555-4555-8555-555555555555', 1, "
                "'2026-02-01', 100000.00, 1000.00, 7884.88, 8884.88, 92115.12);")
        self.refused("INSERT INTO debt_schedule_items (debt_id, installment_no, due_date, opening_balance, interest_amount, "
                     "principal_amount, emi_amount, closing_balance) VALUES ('55555555-5555-4555-8555-555555555555', 2, "
                     "'2026-03-01', 92115.12, 921.15, 7884.88, 8000.00, 84230.24);", "ck_debt_schedule_split")
        self.refused("INSERT INTO debt_schedule_items (debt_id, installment_no, due_date, opening_balance, interest_amount, "
                     "principal_amount, emi_amount, closing_balance) VALUES ('55555555-5555-4555-8555-555555555555', 3, "
                     "'2026-04-01', 84230.24, 842.30, 7000.00, 7842.30, 80000.00);", "ck_debt_schedule_roll")

    def test_debt_interest_type_and_tenure_are_constrained(self):
        self.refused("INSERT INTO debts (name, lender, principal, annual_rate, interest_type, emi, tenure_months, "
                     "start_date, first_due_date, end_date) VALUES ('x','y',1000,5,'compound',100,12,'2026-01-01','2026-02-01','2027-01-01');")
        self.refused("INSERT INTO debts (name, lender, principal, annual_rate, interest_type, emi, tenure_months, "
                     "start_date, first_due_date, end_date) VALUES ('x','y',1000,5,'flat',100,0,'2026-01-01','2026-02-01','2027-01-01');")

    def test_bank_transactions_are_append_only_and_unique_per_account(self):
        account = ("INSERT INTO bank_accounts (id, provider, external_ref, display_name) "
                   "VALUES ('66666666-6666-4666-8666-666666666666', 'fixture-test', 'acc-1', 'Main');")
        txn = ("INSERT INTO bank_transactions (id, bank_account_id, external_id, txn_date, amount, import_key) "
               "VALUES ('77777777-7777-4777-8777-777777777777', '66666666-6666-4666-8666-666666666666', 't1', '2026-10-05', -100.00, 'k1');")
        self.ok(account + txn)
        self.refused(txn.replace("'t1'", "'t1'").replace("'77777777-7777-4777-8777-777777777777'", "'88888888-8888-4888-8888-888888888888'"), "uq_bank_transactions_external")
        self.refused("UPDATE bank_transactions SET amount = -1 WHERE id = '77777777-7777-4777-8777-777777777777';", "append-only")
        self.refused("DELETE FROM bank_transactions WHERE id = '77777777-7777-4777-8777-777777777777';", "append-only")

    def test_bank_account_provider_reference_is_unique(self):
        self.ok("INSERT INTO bank_accounts (provider, external_ref, display_name) VALUES ('fixture-test', 'acc-1', 'Main');")
        self.refused("INSERT INTO bank_accounts (provider, external_ref, display_name) VALUES ('fixture-test', 'acc-1', 'Dup');",
                     "uq_bank_accounts_ref")

    def test_reconciliation_suggestion_may_change_only_its_decision(self):
        self.ok(
            "INSERT INTO bank_accounts (id, provider, external_ref, display_name) VALUES ('99999999-9999-4999-8999-999999999999', 'p', 'r', 'n');"
            "INSERT INTO bank_transactions (id, bank_account_id, external_id, txn_date, amount, import_key) "
            "VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '99999999-9999-4999-8999-999999999999', 'x1', '2026-10-05', -50.00, 'k2');"
            "INSERT INTO reconciliation_suggestions (id, bank_transaction_id, kind, score) VALUES "
            "('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'missing_in_ledger', 0);"
            "UPDATE reconciliation_suggestions SET status = 'rejected', decided_at = now() WHERE id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';"
        )
        self.refused("UPDATE reconciliation_suggestions SET score = 99 WHERE id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';",
                     "only the decision")
        self.refused("DELETE FROM reconciliation_suggestions WHERE id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';", "cannot be deleted")

    def test_notifications_dedupe_and_only_read_state_changes(self):
        self.ok("INSERT INTO notifications (id, kind, title, body, dedupe_key) VALUES "
                "('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'emi_due', 'Due', 'body', 'emi_due:l1:2026-10-05');"
                "UPDATE notifications SET read_at = now() WHERE id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';")
        self.refused("INSERT INTO notifications (kind, title, body, dedupe_key) VALUES "
                     "('emi_due', 'Due again', 'b', 'emi_due:l1:2026-10-05');", "uq_notifications_dedupe")
        self.refused("UPDATE notifications SET body = 'changed' WHERE id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';",
                     "only the read state")
        self.refused("DELETE FROM notifications WHERE id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';", "cannot be deleted")

    def test_document_classification_accepts_finance_types(self):
        self.ok("INSERT INTO document_processing_jobs (classification) VALUES ('emi');"
                "INSERT INTO document_processing_jobs (classification) VALUES ('bank_statement');")
        self.refused("INSERT INTO document_processing_jobs (classification) VALUES ('crypto_slip');", "ck_document_jobs_classification")


if __name__ == "__main__":
    unittest.main()
