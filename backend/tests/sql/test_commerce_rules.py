"""Database-level Phase 3 rules, executed against a fresh PostgreSQL 16 database.

Requires the `psql` client and an administrative connection URI in COMMERCE_SQL_TEST_ADMIN_URI, for example
`postgresql://postgres@/postgres?host=/var/run/postgresql`. The test creates and drops its own disposable
database (never point this at real data). Skipped when the variable is not set.

It proves the triggers and CHECK constraints written in alembic/versions/20261008_0003_commerce.py. It does
not exercise the SQLAlchemy models or the FastAPI routes, which need the full Python dependency set.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "20261008_0003_commerce.py"
ADMIN_URI = os.environ.get("COMMERCE_SQL_TEST_ADMIN_URI")

# Phase 1 and Phase 2 tables that the Phase 3 migration references. Stand-ins only.
STUBS = """
CREATE TABLE owners (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE accounts (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE documents (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE journal_entries (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
"""


def load_statements() -> tuple[str, ...]:
    """Read STATEMENTS from the migration without importing Alembic (the migration only uses op.execute)."""
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("commerce_migration", MIGRATION)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.STATEMENTS
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved


def with_database(uri: str, name: str) -> str:
    """Swap the database name in a URI, keeping host, port and query (including a Unix-socket host parameter)."""
    parts = urlsplit(uri)
    rebuilt = urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
    if not parts.netloc and rebuilt.startswith(f"{parts.scheme}:/") and not rebuilt.startswith(f"{parts.scheme}://"):
        rebuilt = rebuilt.replace(f"{parts.scheme}:/", f"{parts.scheme}:///", 1)
    return rebuilt


def run_psql(uri: str, sql: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write(sql)
        path = handle.name
    try:
        return subprocess.run(
            ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", uri, "-f", path],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        os.unlink(path)


def fresh_id() -> str:
    return str(uuid.uuid4())


def seed(ids: dict[str, str]) -> str:
    """Minimal valid rows for one case. Fixed ids per case keep the SQL readable."""
    return f"""
    INSERT INTO owners (id) VALUES ('{ids['owner']}');
    INSERT INTO accounts (id) VALUES ('{ids['acct_inventory']}'), ('{ids['acct_expense']}'), ('{ids['acct_ap']}');
    INSERT INTO journal_entries (id) VALUES ('{ids['je']}');
    INSERT INTO documents (id) VALUES ('{ids['document']}');
    INSERT INTO product_categories (id, name) VALUES ('{ids['category']}', 'Cat {ids['category'][:8]}');
    INSERT INTO products (id, sku, name, unit_of_measure, category_id)
        VALUES ('{ids['product']}', 'SKU-{ids['product'][:8]}', 'Widget', 'pcs', '{ids['category']}');
    INSERT INTO products (id, sku, name, unit_of_measure)
        VALUES ('{ids['product2']}', 'SKU2-{ids['product'][:8]}', 'Gadget', 'pcs');
    INSERT INTO parties (id, kind, name) VALUES ('{ids['supplier']}', 'supplier', 'Supplier A');
    INSERT INTO parties (id, kind, name) VALUES ('{ids['customer']}', 'customer', 'Customer B');
    INSERT INTO warehouses (id, code, name) VALUES ('{ids['warehouse']}', 'MAIN-{ids['warehouse'][:4].upper()}', 'Main');
    INSERT INTO stock_locations (id, warehouse_id, code, name)
        VALUES ('{ids['location']}', '{ids['warehouse']}', 'BIN1', 'Bin 1'),
               ('{ids['location2']}', '{ids['warehouse']}', 'BIN2', 'Bin 2');
    """


def ids_for_case() -> dict[str, str]:
    names = (
        "owner", "acct_inventory", "acct_expense", "acct_ap", "je", "document", "category", "product",
        "product2", "supplier", "customer", "warehouse", "location", "location2", "po", "po_line", "grn", "grn_line",
        "bill", "bill_line", "so", "so_line", "quote", "job", "pr",
    )
    return {name: fresh_id() for name in names}


def pos_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class CommerceSqlRules(unittest.TestCase):
    """Each case runs in its own transaction. Cases expected to fail assert the failing rule by message."""

    database_uri: str | None = None
    created_database: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if not ADMIN_URI:
            raise unittest.SkipTest("COMMERCE_SQL_TEST_ADMIN_URI is not set")
        name = f"logu_commerce_{uuid.uuid4().hex[:12]}"
        created = run_psql(ADMIN_URI, f"CREATE DATABASE {name};")
        if created.returncode != 0:
            raise unittest.SkipTest(f"cannot create test database: {created.stderr.strip()}")
        cls.created_database = name
        cls.database_uri = with_database(ADMIN_URI, name)
        setup = run_psql(cls.database_uri, STUBS + "\n".join(s.rstrip(";") + ";" for s in load_statements()))
        if setup.returncode != 0:
            run_psql(ADMIN_URI, f"DROP DATABASE IF EXISTS {name};")
            raise AssertionError(f"migration failed: {setup.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.created_database and ADMIN_URI:
            run_psql(ADMIN_URI, f"DROP DATABASE IF EXISTS {cls.created_database};")

    def _expect_ok(self, body: str, *, commit: bool) -> None:
        ids = ids_for_case()
        sql = "BEGIN;\n" + seed(ids) + body.format(**ids) + ("\nCOMMIT;" if commit else "\nROLLBACK;")
        result = run_psql(self.database_uri or "", sql)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _expect_error(self, body: str, message: str, *, commit: bool = False) -> None:
        ids = ids_for_case()
        sql = "BEGIN;\n" + seed(ids) + body.format(**ids) + ("\nCOMMIT;" if commit else "\nROLLBACK;")
        result = run_psql(self.database_uri or "", sql)
        self.assertNotEqual(result.returncode, 0, "expected the database to refuse this change")
        self.assertIn(message, result.stderr)

    # ---------------------------------------------------------------- stock movements

    def test_movement_sign_must_match_type(self) -> None:
        self._expect_error(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost) VALUES ('{product}', '{location}', 'stock_in', -1, 1, 1, 1);",
            "ck_stock_movements_direction",
        )

    def test_zero_quantity_movement_refused(self) -> None:
        self._expect_error(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost) VALUES ('{product}', '{location}', 'stock_in', 0, 0, 0, 1);",
            "ck_stock_movements_direction",
        )

    def test_unknown_movement_type_refused(self) -> None:
        self._expect_error(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after) VALUES ('{product}', '{location}', 'teleport', 1, 1, 0);",
            "ck_stock_movements_direction",  # the direction rule also refuses an unknown type
        )

    def test_stock_movement_is_append_only(self) -> None:
        insert = (
            "INSERT INTO stock_movements (id, product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost) VALUES ('{pr}', '{product}', '{location}', 'stock_in', 5, 5, 10, 10);"
        )
        self._expect_error(insert + "UPDATE stock_movements SET quantity_delta = 6 WHERE id = '{pr}';", "append-only")
        self._expect_error(insert + "DELETE FROM stock_movements WHERE id = '{pr}';", "append-only")
        self._expect_error(insert + "TRUNCATE stock_movements;", "append-only")

    def test_balanced_transfer_commits(self) -> None:
        self._expect_ok(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost, transfer_group_id) VALUES "
            "('{product}', '{location}', 'transfer_out', -3, 0, 10, 10, '{pr}'), "
            "('{product}', '{location2}', 'transfer_in', 3, 3, 10, 10, '{pr}');",
            commit=True,
        )

    def test_single_leg_transfer_refused_at_commit(self) -> None:
        self._expect_error(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost, transfer_group_id) VALUES "
            "('{product}', '{location}', 'transfer_out', -3, 0, 10, 10, '{pr}');",
            "exactly two legs that balance",
            commit=True,
        )

    def test_unbalanced_transfer_refused_at_commit(self) -> None:
        self._expect_error(
            "INSERT INTO stock_movements (product_id, location_id, movement_type, quantity_delta, balance_after, "
            "average_cost_after, unit_cost, transfer_group_id) VALUES "
            "('{product}', '{location}', 'transfer_out', -3, 0, 10, 10, '{pr}'), "
            "('{product}', '{location2}', 'transfer_in', 2, 2, 10, 10, '{pr}');",
            "exactly two legs that balance",
            commit=True,
        )

    def test_negative_balance_refused_by_default(self) -> None:
        self._expect_error(
            "INSERT INTO stock_balances (product_id, location_id, quantity, average_cost) "
            "VALUES ('{product}', '{location}', -1, 10);",
            "negative stock is not allowed",
        )

    def test_negative_balance_allowed_when_warehouse_opts_in(self) -> None:
        self._expect_ok(
            "UPDATE warehouses SET allow_negative_stock = true WHERE id = '{warehouse}';"
            "INSERT INTO stock_balances (product_id, location_id, quantity, average_cost) "
            "VALUES ('{product}', '{location}', -1, 10);",
            commit=False,
        )

    def test_balance_rows_cannot_be_deleted(self) -> None:
        self._expect_error(
            "INSERT INTO stock_balances (product_id, location_id, quantity, average_cost) "
            "VALUES ('{product}', '{location}', 2, 10); DELETE FROM stock_balances;",
            "append-only",
        )

    # ---------------------------------------------------------------- catalogue

    def test_duplicate_sku_refused(self) -> None:
        self._expect_error(
            "INSERT INTO products (id, sku, name, unit_of_measure) VALUES ('{pr}', 'SKU-' || substr('{product}', 1, 8), "
            "'Copy', 'pcs');",
            "products_sku_key",
        )

    def test_sku_format_refused(self) -> None:
        self._expect_error(
            "INSERT INTO products (id, sku, name, unit_of_measure) VALUES ('{pr}', ' bad sku', 'X', 'pcs');",
            "ck_products_sku",
        )

    def test_unit_of_measure_checked(self) -> None:
        self._expect_error(
            "INSERT INTO products (id, sku, name, unit_of_measure) VALUES ('{pr}', 'NEW-1', 'X', 'furlong');",
            "ck_products_uom",
        )

    def test_duplicate_barcode_refused(self) -> None:
        self._expect_error(
            "UPDATE products SET barcode = '8901000001' WHERE id = '{product}';"
            "UPDATE products SET barcode = '8901000001' WHERE id = '{product2}';",
            "products_barcode_key",
        )

    def test_warehouse_code_must_be_uppercase(self) -> None:
        self._expect_error(
            "INSERT INTO warehouses (id, code, name) VALUES ('{pr}', 'lower', 'X');", "ck_warehouses_code"
        )

    def test_location_code_unique_within_warehouse(self) -> None:
        self._expect_error(
            "INSERT INTO stock_locations (id, warehouse_id, code, name) "
            "VALUES ('{pr}', '{warehouse}', 'BIN1', 'Again');",
            "uq_stock_locations_code",
        )

    def test_account_mapping_role_is_fixed(self) -> None:
        self._expect_error(
            "INSERT INTO commerce_account_mappings (role, account_id) VALUES ('mystery', '{acct_inventory}');",
            "ck_commerce_account_mappings_role",
        )

    def test_account_mapping_cannot_be_deleted(self) -> None:
        self._expect_error(
            "INSERT INTO commerce_account_mappings (role, account_id) VALUES ('inventory', '{acct_inventory}');"
            "DELETE FROM commerce_account_mappings;",
            "append-only",
        )

    # ---------------------------------------------------------------- purchasing

    def test_purchase_order_lines_editable_while_draft(self) -> None:
        self._expect_ok(
            "INSERT INTO purchase_orders (id, number, order_date, party_id) VALUES "
            "('{po}', 'PO-T1', '2026-10-01', '{supplier}');"
            "INSERT INTO purchase_order_lines (id, purchase_order_id, line_number, product_id, description, quantity, "
            "rate, line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{po_line}', '{po}', 1, '{product}', 'Widget', 2, 50, 100, 0, 100, 0, 100);",
            commit=False,
        )

    def test_purchase_order_lines_frozen_after_approval(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_orders (id, number, order_date, party_id, status) VALUES "
            "('{po}', 'PO-T2', '2026-10-01', '{supplier}', 'approved');"
            "INSERT INTO purchase_order_lines (id, purchase_order_id, line_number, product_id, description, quantity, "
            "rate, line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{po_line}', '{po}', 1, '{product}', 'Widget', 2, 50, 100, 0, 100, 0, 100);",
            "only drafts are editable",
        )

    def test_purchase_order_figures_frozen_but_status_moves(self) -> None:
        base = (
            "INSERT INTO purchase_orders (id, number, order_date, party_id, status, taxable_total, grand_total) VALUES "
            "('{po}', 'PO-T3', '2026-10-01', '{supplier}', 'approved', 100, 100);"
        )
        self._expect_error(
            base + "UPDATE purchase_orders SET taxable_total = 150, grand_total = 150 WHERE id = '{po}';",
            "no longer a draft",
        )
        self._expect_ok(
            base + "UPDATE purchase_orders SET status = 'partially_received' WHERE id = '{po}';", commit=False
        )

    def test_approved_purchase_order_cannot_be_deleted(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_orders (id, number, order_date, party_id, status) VALUES "
            "('{po}', 'PO-T4', '2026-10-01', '{supplier}', 'approved'); DELETE FROM purchase_orders WHERE id = '{po}';",
            "cannot be deleted once it has left draft",
        )

    def test_goods_receipt_is_append_only(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_orders (id, number, order_date, party_id, status) VALUES "
            "('{po}', 'PO-T5', '2026-10-01', '{supplier}', 'approved');"
            "INSERT INTO goods_receipts (id, number, receipt_date, purchase_order_id, warehouse_id, location_id) VALUES "
            "('{grn}', 'GRN-T1', '2026-10-02', '{po}', '{warehouse}', '{location}');"
            "UPDATE goods_receipts SET notes = 'changed' WHERE id = '{grn}';",
            "append-only",
        )

    def test_posted_bill_figures_are_frozen_and_voiding_needs_reversal(self) -> None:
        draft = (
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id, taxable_total, "
            "grand_total) VALUES ('{bill}', 'BILL-T1', 'SUP-1', '2026-10-03', '{supplier}', 100, 100);"
        )
        self._expect_error(
            draft
            + "UPDATE purchase_bills SET status = 'posted', journal_entry_id = '{je}' WHERE id = '{bill}';"
            + "UPDATE purchase_bills SET taxable_total = 90, grand_total = 90 WHERE id = '{bill}';",
            "no longer a draft",
        )
        self._expect_error(
            draft + "UPDATE purchase_bills SET status = 'voided' WHERE id = '{bill}';",
            "ck_purchase_bills_posted",
        )
        self._expect_error(
            draft
            + "UPDATE purchase_bills SET status = 'posted', journal_entry_id = '{je}' WHERE id = '{bill}';"
            + "UPDATE purchase_bills SET status = 'voided' WHERE id = '{bill}';",
            "ck_purchase_bills_voided",
        )

    def test_voided_bill_cannot_reopen(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id, status, "
            "journal_entry_id, reversal_entry_id, voided_at) VALUES ('{bill}', 'BILL-T2', 'SUP-2', '2026-10-03', "
            "'{supplier}', 'voided', '{je}', '{je}', now());"
            "UPDATE purchase_bills SET status = 'posted' WHERE id = '{bill}';",
            "cannot be reopened",
        )

    def test_duplicate_supplier_invoice_number_refused(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id) VALUES "
            "('{bill}', 'BILL-T3', 'ABC-9', '2026-10-03', '{supplier}');"
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id) VALUES "
            "('{pr}', 'BILL-T4', 'abc-9', '2026-10-04', '{supplier}');",
            "uq_purchase_bills_supplier_invoice",
        )

    def test_bill_line_needs_exactly_one_source(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id) VALUES "
            "('{bill}', 'BILL-T5', 'SUP-5', '2026-10-03', '{supplier}');"
            "INSERT INTO purchase_bill_lines (id, purchase_bill_id, line_number, description, expense_account_id, "
            "goods_receipt_line_id, product_id, quantity, rate, line_subtotal, line_discount, taxable_amount, "
            "tax_amount, line_total) VALUES ('{bill_line}', '{bill}', 1, 'x', '{acct_expense}', '{grn_line}', "
            "'{product}', 1, 1, 1, 0, 1, 0, 1);",
            "ck_purchase_bill_lines_source",
        )

    def test_posted_bill_requires_journal_entry(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, party_id, status) VALUES "
            "('{bill}', 'BILL-T6', 'SUP-6', '2026-10-03', '{supplier}', 'posted');",
            "ck_purchase_bills_posted",
        )

    def test_bill_due_date_cannot_precede_bill_date(self) -> None:
        self._expect_error(
            "INSERT INTO purchase_bills (id, number, supplier_invoice_number, bill_date, due_date, party_id) VALUES "
            "('{bill}', 'BILL-T7', 'SUP-7', '2026-10-03', '2026-10-01', '{supplier}');",
            "ck_purchase_bills_due",
        )

    # ---------------------------------------------------------------- sales

    def test_sales_order_invoiced_quantity_moves_after_confirmation(self) -> None:
        self._expect_ok(
            "INSERT INTO sales_orders (id, number, order_date, party_id) VALUES "
            "('{so}', 'SO-T1', '2026-10-01', '{customer}');"
            "INSERT INTO sales_order_lines (id, sales_order_id, line_number, product_id, description, quantity, rate, "
            "line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{so_line}', '{so}', 1, '{product}', 'Widget', 5, 10, 50, 0, 50, 0, 50);"
            "UPDATE sales_orders SET status = 'confirmed' WHERE id = '{so}';"
            "UPDATE sales_order_lines SET invoiced_quantity = 2 WHERE id = '{so_line}';",
            commit=False,
        )

    def test_sales_order_line_price_frozen_after_confirmation(self) -> None:
        self._expect_error(
            "INSERT INTO sales_orders (id, number, order_date, party_id) VALUES "
            "('{so}', 'SO-T2', '2026-10-01', '{customer}');"
            "INSERT INTO sales_order_lines (id, sales_order_id, line_number, product_id, description, quantity, rate, "
            "line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{so_line}', '{so}', 1, '{product}', 'Widget', 5, 10, 50, 0, 50, 0, 50);"
            "UPDATE sales_orders SET status = 'confirmed' WHERE id = '{so}';"
            "UPDATE sales_order_lines SET rate = 99, line_subtotal = 495, taxable_amount = 495, line_total = 495 "
            "WHERE id = '{so_line}';",
            "confirmed sales order lines cannot be changed",
        )

    def test_invoiced_quantity_cannot_exceed_ordered(self) -> None:
        self._expect_error(
            "INSERT INTO sales_orders (id, number, order_date, party_id) VALUES "
            "('{so}', 'SO-T3', '2026-10-01', '{customer}');"
            "INSERT INTO sales_order_lines (id, sales_order_id, line_number, product_id, description, quantity, rate, "
            "line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{so_line}', '{so}', 1, '{product}', 'Widget', 5, 10, 50, 0, 50, 0, 50);"
            "UPDATE sales_orders SET status = 'confirmed' WHERE id = '{so}';"
            "UPDATE sales_order_lines SET invoiced_quantity = 6 WHERE id = '{so_line}';",
            "ck_sales_order_lines_invoiced",
        )

    def test_invoice_location_needs_warehouse_pair(self) -> None:
        self._expect_error(
            "INSERT INTO sales_orders (id, number, order_date, party_id, status) VALUES "
            "('{so}', 'SO-T4', '2026-10-01', '{customer}', 'confirmed');"
            "INSERT INTO sales_invoices (id, number, invoice_date, party_id, sales_order_id, location_id) VALUES "
            "('{bill}', 'INV-T1', '2026-10-02', '{customer}', '{so}', '{location}');",
            "ck_sales_invoices_location",
        )

    def test_posted_invoice_requires_journal_entry(self) -> None:
        self._expect_error(
            "INSERT INTO sales_orders (id, number, order_date, party_id, status) VALUES "
            "('{so}', 'SO-T5', '2026-10-01', '{customer}', 'confirmed');"
            "INSERT INTO sales_invoices (id, number, invoice_date, party_id, sales_order_id, status) VALUES "
            "('{bill}', 'INV-T2', '2026-10-02', '{customer}', '{so}', 'posted');",
            "ck_sales_invoices_posted",
        )

    def test_invoice_lines_frozen_after_posting(self) -> None:
        self._expect_error(
            "INSERT INTO sales_orders (id, number, order_date, party_id) VALUES "
            "('{so}', 'SO-T6', '2026-10-01', '{customer}');"
            "INSERT INTO sales_order_lines (id, sales_order_id, line_number, product_id, description, quantity, rate, "
            "line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{so_line}', '{so}', 1, '{product}', 'Widget', 5, 10, 50, 0, 50, 0, 50);"
            "UPDATE sales_orders SET status = 'confirmed' WHERE id = '{so}';"
            "INSERT INTO sales_invoices (id, number, invoice_date, party_id, sales_order_id, status, journal_entry_id) "
            "VALUES ('{bill}', 'INV-T3', '2026-10-02', '{customer}', '{so}', 'posted', '{je}');"
            "INSERT INTO sales_invoice_lines (id, sales_invoice_id, line_number, sales_order_line_id, description, "
            "quantity, rate, line_subtotal, line_discount, taxable_amount, tax_amount, line_total) VALUES "
            "('{bill_line}', '{bill}', 1, '{so_line}', 'Widget', 1, 10, 10, 0, 10, 0, 10);",
            "only drafts are editable",
        )

    # ---------------------------------------------------------------- document processing

    def test_approved_job_needs_target_document(self) -> None:
        self._expect_error(
            "INSERT INTO document_processing_jobs (id, document_id, status, provider_name) VALUES "
            "('{job}', '{document}', 'approved', 'unavailable');",
            "ck_document_jobs_approved",
        )

    def test_one_processing_job_per_document(self) -> None:
        self._expect_error(
            "INSERT INTO document_processing_jobs (id, document_id, provider_name) VALUES ('{job}', '{document}', 'x');"
            "INSERT INTO document_processing_jobs (id, document_id, provider_name) VALUES ('{pr}', '{document}', 'x');",
            "document_processing_jobs_document_id_key",
        )

    def test_classification_confidence_bounded(self) -> None:
        self._expect_error(
            "INSERT INTO document_processing_jobs (id, document_id, provider_name, classification_confidence) "
            "VALUES ('{job}', '{document}', 'x', 1.5);",
            "ck_document_jobs_confidence",
        )

    def test_unknown_classification_refused(self) -> None:
        self._expect_error(
            "INSERT INTO document_processing_jobs (id, document_id, provider_name, classification) "
            "VALUES ('{job}', '{document}', 'x', 'bank_statement');",
            "ck_document_jobs_classification",
        )


if __name__ == "__main__":
    unittest.main()
