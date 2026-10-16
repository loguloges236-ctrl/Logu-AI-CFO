"""Migration 0008 database rules: settlement request lifecycle, payment journal and reference idempotency.

Runs on PostgreSQL against the full chain 0004 -> 0008 with minimal parent stubs. Skipped unless
COMMERCE_SQL_TEST_ADMIN_URI is set.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import uuid
from pathlib import Path

from tests.sql.test_commerce_rules import ADMIN_URI, run_psql, with_database
from tests.sql.test_finance_rules import load_statements as load_0004

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "alembic" / "versions"
LATER = (
    VERSIONS / "20261008_0005_settlements.py",
    VERSIONS / "20261008_0006_settlement_journal_link.py",
    VERSIONS / "20261008_0007_finance_documents.py",
    VERSIONS / "20261008_0008_finance_lifecycle.py",
)

PARENTS = """
CREATE TABLE accounts (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE journal_entries (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE document_processing_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classification varchar(30),
    CONSTRAINT ck_document_jobs_classification CHECK (classification IS NULL OR classification IN ('purchase_bill'))
);
CREATE TABLE sales_invoices (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), status varchar(20) NOT NULL,
    grand_total numeric(18,2) NOT NULL, journal_entry_id uuid REFERENCES journal_entries(id));
CREATE TABLE purchase_bills (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), status varchar(20) NOT NULL,
    grand_total numeric(18,2) NOT NULL, journal_entry_id uuid REFERENCES journal_entries(id));
"""

INV = "'21212121-2121-4212-8212-212121212121'"
CASH = "'31313131-3131-4313-8313-313131313131'"
CONTROL = "'41414141-4141-4414-8414-414141414141'"
OBLIGATION = "'51515151-5151-4515-8515-515151515151'"


def load_chain() -> tuple[str, ...]:
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    statements: list[str] = list(load_0004())
    try:
        for index, path in enumerate(LATER):
            spec = importlib.util.spec_from_file_location(f"lifecycle_chain_{index}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            statements.extend(module.STATEMENTS)
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved
    return tuple(statements)


@unittest.skipUnless(ADMIN_URI, "COMMERCE_SQL_TEST_ADMIN_URI is not set")
class FinanceLifecycleRuleTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"logu_life_{uuid.uuid4().hex[:10]}"
        create = run_psql(ADMIN_URI, f'CREATE DATABASE "{self.db_name}";')
        assert create.returncode == 0, create.stderr
        self.uri = with_database(ADMIN_URI, self.db_name)
        setup = run_psql(self.uri, PARENTS + "\n".join(s + ";" for s in load_chain()))
        assert setup.returncode == 0, setup.stderr
        self.ok(f"INSERT INTO accounts (id) VALUES ({CASH}), ({CONTROL});"
                f"INSERT INTO sales_invoices (id, status, grand_total) VALUES ({INV}, 'posted', 1000.00);")

    def tearDown(self):
        run_psql(ADMIN_URI, f'DROP DATABASE IF EXISTS "{self.db_name}";')

    def ok(self, sql):
        r = run_psql(self.uri, sql)
        self.assertEqual(r.returncode, 0, r.stderr)

    def refused(self, sql, fragment=""):
        r = run_psql(self.uri, sql)
        self.assertNotEqual(r.returncode, 0, "statement should have been refused")
        if fragment:
            self.assertIn(fragment, r.stderr)

    REQUEST = "'61616161-6161-4616-8616-616161616161'"

    def request_sql(self, rid=REQUEST, reference="R-1", amount="400.00"):
        return (f"INSERT INTO settlement_requests (id, direction, sales_invoice_id, amount, settled_on, method, reference, "
                f"cash_account_id, control_account_id) VALUES ({rid}, 'receivable', {INV}, {amount}, '2026-10-08', 'bank', "
                f"'{reference}', {CASH}, {CONTROL});")

    def test_pending_request_can_be_approved_once_with_its_settlement(self):
        self.ok(self.request_sql())
        self.ok("INSERT INTO journal_entries (id) VALUES ('71717171-7171-4717-8717-717171717171');"
                "INSERT INTO settlements (id, direction, sales_invoice_id, amount, settled_on, method, journal_entry_id) "
                f"VALUES ('81818181-8181-4818-8818-818181818181', 'receivable', {INV}, 400.00, '2026-10-08', 'bank', "
                "'71717171-7171-4717-8717-717171717171');")
        self.ok("UPDATE settlement_requests SET status = 'approved', decided_at = now(), "
                f"settlement_id = '81818181-8181-4818-8818-818181818181' WHERE id = {self.REQUEST};")

    def test_approval_without_a_settlement_is_refused(self):
        self.ok(self.request_sql())
        self.refused(f"UPDATE settlement_requests SET status = 'approved', decided_at = now() WHERE id = {self.REQUEST};",
                     "ck_settlement_request_decision")

    def test_rejection_is_final(self):
        self.ok(self.request_sql())
        self.ok(f"UPDATE settlement_requests SET status = 'rejected', decided_at = now() WHERE id = {self.REQUEST};")
        self.refused(f"UPDATE settlement_requests SET status = 'approved', decided_at = now() WHERE id = {self.REQUEST};",
                     "decided settlement request is final")

    def test_requests_cannot_be_deleted_or_edited_before_decision(self):
        self.ok(self.request_sql())
        self.refused(f"DELETE FROM settlement_requests WHERE id = {self.REQUEST};", "cannot be deleted")
        self.refused(f"UPDATE settlement_requests SET amount = 1.00 WHERE id = {self.REQUEST};", "only the decision")

    def test_request_needs_exactly_one_document_and_positive_amount(self):
        self.refused(self.request_sql(amount="0.00"), "settlement_requests_amount_check")
        self.refused(f"INSERT INTO settlement_requests (direction, sales_invoice_id, purchase_bill_id, amount, settled_on, "
                     f"method, reference, cash_account_id, control_account_id) VALUES ('receivable', {INV}, {INV}, 5.00, "
                     f"'2026-10-08', 'bank', 'X', {CASH}, {CONTROL});", "one_document")

    def test_payment_journal_cannot_back_two_payments(self):
        self.ok("INSERT INTO journal_entries (id) VALUES ('91919191-9191-4919-8919-919191919191');"
                f"INSERT INTO payment_obligations (id, kind, title, amount, frequency, first_due_date) VALUES ({OBLIGATION}, "
                "'rent', 'Rent', 25000.00, 'monthly', '2026-10-05');")
        pay = ("INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on, journal_entry_id, reference) "
               f"VALUES ({OBLIGATION}, '2026-10-05', 1000.00, '2026-10-05', '91919191-9191-4919-8919-919191919191', 'P-1');")
        self.ok(pay)
        self.refused(pay.replace("'P-1'", "'P-2'"), "uq_payment_records_journal")

    def test_a_retried_payment_reference_cannot_post_twice(self):
        self.ok("INSERT INTO payment_obligations (id, kind, title, amount, frequency, first_due_date) VALUES "
                f"({OBLIGATION}, 'rent', 'Rent', 25000.00, 'monthly', '2026-10-05');")
        first = ("INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on, reference) "
                 f"VALUES ({OBLIGATION}, '2026-10-05', 1000.00, '2026-10-05', 'RENT-OCT');")
        self.ok(first)
        self.refused(first, "uq_payment_records_reference")

    def test_reversal_may_reuse_the_original_reference(self):
        self.ok("INSERT INTO payment_obligations (id, kind, title, amount, frequency, first_due_date) VALUES "
                f"({OBLIGATION}, 'rent', 'Rent', 25000.00, 'monthly', '2026-10-05');"
                "INSERT INTO payment_records (id, obligation_id, period_due_date, amount, paid_on, reference) VALUES "
                f"('a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1', {OBLIGATION}, '2026-10-05', 1000.00, '2026-10-05', 'RENT-OCT');")
        self.ok(f"INSERT INTO payment_records (obligation_id, period_due_date, amount, paid_on, reference, reverses_record_id) "
                f"VALUES ({OBLIGATION}, '2026-10-05', -1000.00, '2026-10-06', 'RENT-OCT', 'a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1');")


if __name__ == "__main__":
    unittest.main()
