"""Settlement (receivable / payable) database rules, run against a disposable PostgreSQL database.

Skipped unless COMMERCE_SQL_TEST_ADMIN_URI is set. Parent tables are minimal stubs that carry exactly the columns the
settlement trigger reads (status, grand_total, journal_entry_id); the Phase 3 migration itself is covered by
tests/sql/test_commerce_rules.py.
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
MIGRATIONS = (
    BACKEND / "alembic" / "versions" / "20261008_0005_settlements.py",
    BACKEND / "alembic" / "versions" / "20261008_0006_settlement_journal_link.py",
)

STUBS = """
CREATE TABLE journal_entries (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE sales_invoices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status varchar(20) NOT NULL,
    grand_total numeric(18,2) NOT NULL,
    journal_entry_id uuid REFERENCES journal_entries(id)
);
CREATE TABLE purchase_bills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status varchar(20) NOT NULL,
    grand_total numeric(18,2) NOT NULL,
    journal_entry_id uuid REFERENCES journal_entries(id)
);
INSERT INTO sales_invoices (id, status, grand_total) VALUES
    ('22222222-2222-4222-8222-222222222222', 'posted', 1000.00),
    ('22222222-2222-4222-8222-222222222223', 'draft', 500.00);
INSERT INTO purchase_bills (id, status, grand_total) VALUES
    ('33333333-3333-4333-8333-333333333333', 'posted', 800.00);
"""

INV = "'22222222-2222-4222-8222-222222222222'"
DRAFT_INV = "'22222222-2222-4222-8222-222222222223'"
BILL = "'33333333-3333-4333-8333-333333333333'"


def load_statements() -> tuple[str, ...]:
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    statements: list[str] = []
    try:
        for index, path in enumerate(MIGRATIONS):
            spec = importlib.util.spec_from_file_location(f"settlement_migration_{index}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            statements.extend(module.STATEMENTS)
        return tuple(statements)
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved


def receive(amount: str, doc: str = INV, sid: str | None = None, reverses: str | None = None) -> str:
    column = "purchase_bill_id" if doc == BILL else "sales_invoice_id"
    direction = "payable" if doc == BILL else "receivable"
    id_part = f"'{sid}'" if sid else "gen_random_uuid()"
    rev_part = f"'{reverses}'" if reverses else "NULL"
    return (
        f"INSERT INTO settlements (id, direction, {column}, amount, settled_on, method, reverses_settlement_id) "
        f"VALUES ({id_part}, '{direction}', {doc}, {amount}, '2026-10-08', 'bank', {rev_part});"
    )


@unittest.skipUnless(ADMIN_URI, "COMMERCE_SQL_TEST_ADMIN_URI is not set")
class SettlementDatabaseRuleTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"logu_set_{uuid.uuid4().hex[:10]}"
        create = run_psql(ADMIN_URI, f'CREATE DATABASE "{self.db_name}";')
        assert create.returncode == 0, create.stderr
        self.uri = with_database(ADMIN_URI, self.db_name)
        setup = run_psql(self.uri, STUBS + "\n".join(s + ";" for s in load_statements()))
        assert setup.returncode == 0, setup.stderr

    def tearDown(self):
        run_psql(ADMIN_URI, f'DROP DATABASE IF EXISTS "{self.db_name}";')

    def ok(self, statements: str):
        result = run_psql(self.uri, statements)
        self.assertEqual(result.returncode, 0, result.stderr)

    def refused(self, statements: str, fragment: str = ""):
        result = run_psql(self.uri, statements)
        self.assertNotEqual(result.returncode, 0, "statement should have been refused")
        if fragment:
            self.assertIn(fragment, result.stderr)

    def test_partial_receipts_accumulate_up_to_the_total(self):
        self.ok(receive("400.00") + receive("600.00"))
        self.refused(receive("0.01"), "would exceed the document total")

    def test_overpayment_is_refused(self):
        self.refused(receive("1000.01"), "would exceed the document total")

    def test_draft_document_cannot_be_settled(self):
        self.refused(receive("100.00", doc=DRAFT_INV), "only posted documents can be settled")

    def test_payable_settles_a_posted_bill(self):
        self.ok(receive("800.00", doc=BILL))
        self.refused(receive("0.01", doc=BILL), "would exceed the document total")

    def test_exactly_one_document_is_required(self):
        self.refused(
            "INSERT INTO settlements (direction, sales_invoice_id, purchase_bill_id, amount, settled_on, method) "
            f"VALUES ('receivable', {INV}, {BILL}, 10.00, '2026-10-08', 'bank');",
            "ck_settlement_one_document",
        )

    def test_direction_must_match_the_document(self):
        self.refused(
            "INSERT INTO settlements (direction, purchase_bill_id, amount, settled_on, method) "
            f"VALUES ('receivable', {BILL}, 10.00, '2026-10-08', 'bank');",
            "direction does not match",
        )

    def test_zero_and_unknown_methods_are_refused(self):
        self.refused(receive("0.00"), "settlements_amount_check")
        self.refused(
            "INSERT INTO settlements (direction, sales_invoice_id, amount, settled_on, method) "
            f"VALUES ('receivable', {INV}, 10.00, '2026-10-08', 'crypto');",
            "method",
        )

    def test_reversal_restores_the_outstanding_amount(self):
        original = "44444444-4444-4444-8444-444444444444"
        self.ok(receive("400.00", sid=original))
        self.ok(receive("-400.00", reverses=original))
        # Outstanding is back to 1000, so the full total can be settled again.
        self.ok(receive("1000.00"))

    def test_reversal_cannot_exceed_the_original(self):
        original = "44444444-4444-4444-8444-444444444445"
        self.ok(receive("400.00", sid=original))
        self.refused(receive("-500.00", reverses=original), "reversals cannot exceed the original settlement")

    def test_reversal_of_a_reversal_is_refused(self):
        original = "44444444-4444-4444-8444-444444444446"
        reversal = "44444444-4444-4444-8444-444444444447"
        self.ok(receive("400.00", sid=original))
        self.ok(receive("-400.00", sid=reversal, reverses=original))
        self.refused(receive("400.00", reverses=reversal), "only a positive settlement can be reversed")

    def test_net_settlement_can_never_go_negative(self):
        self.refused(receive("-10.00"), "net settlement for this document cannot be negative")

    def test_one_journal_entry_cannot_back_two_settlements(self):
        journal = "66666666-6666-4666-8666-666666666666"
        self.ok(f"INSERT INTO journal_entries (id) VALUES ('{journal}');")
        self.ok(receive("100.00", sid="77777777-7777-4777-8777-777777777777").replace(
            "method, reverses_settlement_id) VALUES", "method, reverses_settlement_id, journal_entry_id) VALUES").replace(
            "'bank', NULL);", f"'bank', NULL, '{journal}');"))
        self.refused(receive("100.00").replace(
            "method, reverses_settlement_id) VALUES", "method, reverses_settlement_id, journal_entry_id) VALUES").replace(
            "'bank', NULL);", f"'bank', NULL, '{journal}');"), "uq_settlements_journal")

    def test_rows_are_append_only(self):
        self.ok(receive("100.00", sid="55555555-5555-4555-8555-555555555555"))
        self.refused(
            "UPDATE settlements SET amount = 999.00 WHERE id = '55555555-5555-4555-8555-555555555555';",
            "append-only",
        )
        self.refused("DELETE FROM settlements;", "append-only")
        self.refused("TRUNCATE settlements;", "append-only")


if __name__ == "__main__":
    unittest.main()
