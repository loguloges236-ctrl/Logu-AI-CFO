"""Migration 0007 database rules: new classification types, near-miss reconciliation kinds, and one debt or obligation
per processing job. Runs on PostgreSQL. Skipped unless COMMERCE_SQL_TEST_ADMIN_URI is set."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import uuid
from pathlib import Path

from tests.sql.test_commerce_rules import ADMIN_URI, run_psql, with_database
from tests.sql.test_finance_rules import STUBS, load_statements as load_0004

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION_0007 = BACKEND / "alembic" / "versions" / "20261008_0007_finance_documents.py"
JOB = "88888888-8888-4888-8888-888888888888"
JOB2 = "99999999-9999-4999-8999-999999999999"


def load_0007() -> tuple[str, ...]:
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("finance_documents_migration", MIGRATION_0007)
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
class FinanceDocumentRuleTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"logu_fdoc_{uuid.uuid4().hex[:10]}"
        create = run_psql(ADMIN_URI, f'CREATE DATABASE "{self.db_name}";')
        assert create.returncode == 0, create.stderr
        self.uri = with_database(ADMIN_URI, self.db_name)
        script = STUBS + "\n".join(s + ";" for s in load_0004()) + "\n" + "\n".join(s + ";" for s in load_0007())
        setup = run_psql(self.uri, script)
        assert setup.returncode == 0, setup.stderr
        self.ok(f"INSERT INTO document_processing_jobs (id, classification) VALUES ('{JOB}', 'emi'), ('{JOB2}', 'debt');")

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

    def test_debt_and_recurring_payment_types_are_classifiable(self):
        self.ok("INSERT INTO document_processing_jobs (classification) VALUES ('recurring_payment');")
        self.refused("INSERT INTO document_processing_jobs (classification) VALUES ('wire_transfer');",
                     "ck_document_jobs_classification")

    def test_reconciliation_near_miss_kinds_are_stored_and_unknown_kinds_refused(self):
        self.ok("INSERT INTO bank_accounts (id, provider, external_ref, display_name) "
                "VALUES ('12121212-1212-4121-8121-121212121212', 'fixture', 'A1', 'Main');")
        self.ok("INSERT INTO bank_transactions (id, bank_account_id, external_id, txn_date, amount, import_key) "
                "VALUES ('13131313-1313-4131-8131-131313131313', '12121212-1212-4121-8121-121212121212', 'T1', "
                "'2026-10-01', -5000.00, 'k1');")
        self.ok("INSERT INTO reconciliation_suggestions (bank_transaction_id, kind, score) "
                "VALUES ('13131313-1313-4131-8131-131313131313', 'date_mismatch', 0);")
        self.refused("INSERT INTO reconciliation_suggestions (bank_transaction_id, kind, score) "
                     "VALUES ('13131313-1313-4131-8131-131313131313', 'guessed', 0);", "kind_check")

    def test_one_obligation_per_processing_job(self):
        first = ("INSERT INTO payment_obligations (kind, title, amount, frequency, first_due_date, source_job_id) "
                 f"VALUES ('emi', 'EMI A', 1000.00, 'once', '2026-10-10', '{JOB}');")
        self.ok(first)
        self.refused(first, "uq_obligations_source_job")

    def test_one_debt_per_processing_job_and_nulls_are_unconstrained(self):
        debt = ("INSERT INTO debts (name, lender, principal, annual_rate, interest_type, emi, tenure_months, "
                "start_date, first_due_date, end_date, source_job_id) VALUES ('Loan', 'Bank', 100000.00, 10, 'reducing', "
                f"8800.00, 12, '2026-09-01', '2026-10-01', '2027-09-01', '{JOB2}');")
        self.ok(debt)
        self.refused(debt, "uq_debts_source_job")
        self.ok("INSERT INTO payment_obligations (kind, title, amount, frequency, first_due_date) "
                "VALUES ('other', 'Manual 1', 10.00, 'once', '2026-10-10'), "
                "('other', 'Manual 2', 10.00, 'once', '2026-10-10');")


if __name__ == "__main__":
    unittest.main()
