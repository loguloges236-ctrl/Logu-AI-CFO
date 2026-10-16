"""Phase 3 commerce API flows against PostgreSQL.

UNVERIFIED in the environment where Phase 3 was written: PostgreSQL-backed FastAPI tests need SQLAlchemy,
FastAPI and psycopg, which could not be installed there. These tests are written to run with
`pytest tests/integration` and a disposable TEST_DATABASE_URL. Do not treat them as passing until executed.

Expected status codes follow app/api/routes/commerce.py: 409 for state or stock conflicts, 422 for invalid input.
"""

from __future__ import annotations

import pytest

from tests.conftest import csrf_headers

API = "/api/v1"
COMMERCE = f"{API}/commerce"
ACCOUNTING = f"{API}/accounting"

# Roles mapped to accounts the owner creates. The default chart has no tax, COGS or default-expense accounts.
EXTRA_ACCOUNTS = [
    {"code": "1500", "name": "Input GST", "category": "asset", "subtype": "general"},
    {"code": "2300", "name": "Output GST", "category": "liability", "subtype": "general"},
    {"code": "5400", "name": "Cost of goods sold", "category": "expense", "subtype": "general"},
    {"code": "5900", "name": "General expense", "category": "expense", "subtype": "general"},
]


def _accounts(client) -> dict[str, str]:
    response = client.get(f"{ACCOUNTING}/accounts")
    assert response.status_code == 200, response.text
    return {a["code"]: a["id"] for a in response.json()}


@pytest.fixture()
def books(owner_client):
    """Chart, October period, extra tax/COGS accounts, and every commerce role mapped."""
    assert owner_client.post(f"{ACCOUNTING}/setup", headers=csrf_headers(owner_client)).status_code == 201
    period = owner_client.post(
        f"{ACCOUNTING}/periods",
        json={"name": "Oct 2026", "start_date": "2026-10-01", "end_date": "2026-10-31"},
        headers=csrf_headers(owner_client),
    )
    assert period.status_code == 201, period.text
    for account in EXTRA_ACCOUNTS:
        response = owner_client.post(f"{ACCOUNTING}/accounts", json=account, headers=csrf_headers(owner_client))
        assert response.status_code == 201, response.text
    codes = _accounts(owner_client)
    roles = {
        "inventory": "1400",
        "accounts_receivable": "1300",
        "input_tax": "1500",
        "accounts_payable": "2100",
        "output_tax": "2300",
        "sales_revenue": "4100",
        "cost_of_goods_sold": "5400",
        "default_expense": "5900",
    }
    for role, code in roles.items():
        response = owner_client.put(
            f"{COMMERCE}/accounting-roles/{role}", json={"account_id": codes[code]}, headers=csrf_headers(owner_client)
        )
        assert response.status_code == 200, response.text
    return codes


@pytest.fixture()
def stock_setup(owner_client, books):
    """A warehouse with one location, one stock item, one supplier and one customer, with 10 units received."""
    h = csrf_headers(owner_client)
    warehouse = owner_client.post(
        f"{COMMERCE}/warehouses", json={"code": "MAIN", "name": "Main store", "allow_negative_stock": False}, headers=h
    )
    assert warehouse.status_code == 201, warehouse.text
    location = owner_client.post(
        f"{COMMERCE}/locations", json={"warehouse_id": warehouse.json()["id"], "code": "SHELF1", "name": "Shelf 1"}, headers=h
    )
    assert location.status_code == 201, location.text
    product = owner_client.post(
        f"{COMMERCE}/products",
        json={
            "sku": "SKU-1", "name": "Widget", "unit_of_measure": "pcs", "is_stock_item": True,
            "purchase_price": "100.00", "selling_price": "150.00", "tax_rate": "18", "hsn_sac": "8471",
        },
        headers=h,
    )
    assert product.status_code == 201, product.text
    supplier = owner_client.post(f"{COMMERCE}/parties", json={"kind": "supplier", "name": "Supplier One"}, headers=h)
    customer = owner_client.post(f"{COMMERCE}/parties", json={"kind": "customer", "name": "Customer One"}, headers=h)
    assert supplier.status_code == 201 and customer.status_code == 201
    received = owner_client.post(
        f"{COMMERCE}/stock/receive",
        json={"product_id": product.json()["id"], "location_id": location.json()["id"], "quantity": "10", "unit_cost": "100.0000"},
        headers=h,
    )
    assert received.status_code == 201, received.text
    return {
        "product": product.json()["id"],
        "warehouse": warehouse.json()["id"],
        "location": location.json()["id"],
        "supplier": supplier.json()["id"],
        "customer": customer.json()["id"],
    }


# ---------------------------------------------------------------- products

def test_duplicate_sku_is_refused(owner_client, books):
    body = {"sku": "DUP-1", "name": "First", "unit_of_measure": "pcs", "is_stock_item": True}
    assert owner_client.post(f"{COMMERCE}/products", json=body, headers=csrf_headers(owner_client)).status_code == 201
    again = owner_client.post(f"{COMMERCE}/products", json=body, headers=csrf_headers(owner_client))
    assert again.status_code == 409, again.text


def test_invalid_money_format_is_refused(owner_client, books):
    response = owner_client.post(
        f"{COMMERCE}/products",
        json={"sku": "BAD-1", "name": "Bad price", "unit_of_measure": "pcs", "is_stock_item": True, "purchase_price": 100.5},
        headers=csrf_headers(owner_client),
    )
    assert response.status_code == 422, response.text


def test_inactive_product_cannot_be_received(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    owner_client.patch(f"{COMMERCE}/products/{stock_setup['product']}", json={"is_active": False}, headers=h)
    response = owner_client.post(
        f"{COMMERCE}/stock/receive",
        json={"product_id": stock_setup["product"], "location_id": stock_setup["location"], "quantity": "1", "unit_cost": "10"},
        headers=h,
    )
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------- inventory

def test_negative_stock_is_refused_by_default(owner_client, stock_setup):
    response = owner_client.post(
        f"{COMMERCE}/stock/issue",
        json={"product_id": stock_setup["product"], "location_id": stock_setup["location"], "quantity": "11"},
        headers=csrf_headers(owner_client),
    )
    assert response.status_code == 409, response.text


def test_damage_and_return_create_traceable_movements(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    damage = owner_client.post(
        f"{COMMERCE}/stock/damage",
        json={"product_id": stock_setup["product"], "location_id": stock_setup["location"], "quantity": "2", "reason": "Broken"},
        headers=h,
    )
    assert damage.status_code == 201, damage.text
    assert damage.json()["movement_type"] == "damage_out"
    returned = owner_client.post(
        f"{COMMERCE}/stock/customer-return",
        json={"product_id": stock_setup["product"], "location_id": stock_setup["location"], "quantity": "1", "reason": "Returned"},
        headers=h,
    )
    assert returned.status_code == 201, returned.text
    balances = owner_client.get(f"{COMMERCE}/stock/balances?product_id={stock_setup['product']}").json()
    assert balances[0]["quantity"] == "9.000"


def test_transfer_balances_and_reconciliation_adjusts_by_movement(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    second = owner_client.post(
        f"{COMMERCE}/locations", json={"warehouse_id": stock_setup["warehouse"], "code": "SHELF2", "name": "Shelf 2"}, headers=h
    ).json()
    transfer = owner_client.post(
        f"{COMMERCE}/stock/transfer",
        json={"product_id": stock_setup["product"], "from_location_id": stock_setup["location"], "to_location_id": second["id"], "quantity": "4"},
        headers=h,
    )
    assert transfer.status_code == 201, transfer.text
    legs = transfer.json()["legs"]
    assert {leg["movement_type"] for leg in legs} == {"transfer_out", "transfer_in"}
    count = owner_client.post(
        f"{COMMERCE}/stock/reconcile",
        json={"product_id": stock_setup["product"], "location_id": second["id"], "counted_quantity": "3", "reason": "Count"},
        headers=h,
    )
    assert count.status_code == 200, count.text
    assert owner_client.get(f"{COMMERCE}/stock/integrity").json()["ok"] is True


# ---------------------------------------------------------------- purchase: PR -> PO -> GRN (partial) -> bill -> accounts

def test_purchase_chain_with_partial_receipt_and_posted_bill(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    request = owner_client.post(
        f"{COMMERCE}/purchase-requests",
        json={"request_date": "2026-10-05", "lines": [{"product_id": stock_setup["product"], "quantity": "5"}]},
        headers=h,
    )
    assert request.status_code == 201, request.text
    approved = owner_client.post(f"{COMMERCE}/purchase-requests/{request.json()['id']}/decision?decision=approve", headers=h)
    assert approved.status_code == 200, approved.text

    order = owner_client.post(
        f"{COMMERCE}/purchase-orders",
        json={
            "party_id": stock_setup["supplier"], "order_date": "2026-10-05", "purchase_request_id": request.json()["id"],
            "lines": [{"product_id": stock_setup["product"], "quantity": "5", "rate": "100.00", "tax_rate": "18"}],
        },
        headers=h,
    )
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    po_line = order.json()["lines"][0]["id"]
    assert owner_client.post(f"{COMMERCE}/purchase-orders/{order_id}/status?status=approved", headers=h).status_code == 200

    partial = owner_client.post(
        f"{COMMERCE}/goods-receipts",
        json={
            "purchase_order_id": order_id, "receipt_date": "2026-10-06", "warehouse_id": stock_setup["warehouse"],
            "location_id": stock_setup["location"], "lines": [{"purchase_order_line_id": po_line, "quantity": "2"}],
        },
        headers=h,
    )
    assert partial.status_code == 201, partial.text
    summary = owner_client.get(f"{COMMERCE}/purchase-orders/{order_id}").json()["receipt_summary"]
    assert summary[0]["received"] == "2.000"

    over = owner_client.post(
        f"{COMMERCE}/goods-receipts",
        json={
            "purchase_order_id": order_id, "receipt_date": "2026-10-06", "warehouse_id": stock_setup["warehouse"],
            "location_id": stock_setup["location"], "lines": [{"purchase_order_line_id": po_line, "quantity": "9"}],
        },
        headers=h,
    )
    assert over.status_code == 409, over.text

    receipt = owner_client.get(f"{COMMERCE}/goods-receipts/{partial.json()['id']}").json()
    bill = owner_client.post(
        f"{COMMERCE}/purchase-bills",
        json={
            "party_id": stock_setup["supplier"], "supplier_invoice_number": "INV-2026-001", "bill_date": "2026-10-07",
            "purchase_order_id": order_id,
            "lines": [{"goods_receipt_line_id": receipt["lines"][0]["id"], "quantity": "2"}],
        },
        headers=h,
    )
    assert bill.status_code == 201, bill.text
    posted = owner_client.post(f"{COMMERCE}/purchase-bills/{bill.json()['id']}/post", headers=h)
    assert posted.status_code == 200, posted.text
    entry_id = posted.json()["journal_entry_id"]
    assert entry_id is not None
    entry = owner_client.get(f"{ACCOUNTING}/journal-entries/{entry_id}").json()
    assert entry["status"] == "posted"


# ---------------------------------------------------------------- sales: quotation -> order -> invoice -> accounts

def test_sales_chain_with_partial_invoice_reduces_stock_and_posts(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    quote = owner_client.post(
        f"{COMMERCE}/quotations",
        json={
            "party_id": stock_setup["customer"], "quotation_date": "2026-10-05",
            "lines": [{"product_id": stock_setup["product"], "quantity": "3", "rate": "150.00", "tax_rate": "18"}],
        },
        headers=h,
    )
    assert quote.status_code == 201, quote.text
    qid = quote.json()["id"]
    for target in ("sent", "accepted"):
        assert owner_client.post(f"{COMMERCE}/quotations/{qid}/status", json={"status": target}, headers=h).status_code == 200
    converted = owner_client.post(f"{COMMERCE}/quotations/{qid}/convert", headers=h)
    assert converted.status_code == 201, converted.text
    order_id = converted.json()["id"]
    so_line = converted.json()["lines"][0]["id"]
    assert owner_client.post(f"{COMMERCE}/sales-orders/{order_id}/status", json={"status": "confirmed"}, headers=h).status_code == 200

    invoice = owner_client.post(
        f"{COMMERCE}/sales-invoices",
        json={
            "sales_order_id": order_id, "invoice_date": "2026-10-08", "warehouse_id": stock_setup["warehouse"],
            "location_id": stock_setup["location"], "lines": [{"sales_order_line_id": so_line, "quantity": "1"}],
        },
        headers=h,
    )
    assert invoice.status_code == 201, invoice.text
    posted = owner_client.post(f"{COMMERCE}/sales-invoices/{invoice.json()['id']}/post", headers=h)
    assert posted.status_code == 200, posted.text
    assert posted.json()["journal_entry_id"] is not None
    assert posted.json()["cost_of_goods_total"] is not None

    balances = owner_client.get(f"{COMMERCE}/stock/balances?product_id={stock_setup['product']}").json()
    assert balances[0]["quantity"] == "9.000"

    order = owner_client.get(f"{COMMERCE}/sales-orders/{order_id}").json()
    assert order["lines"][0]["invoiced_quantity"] == "1.000"
    assert order["status"] == "partially_invoiced"


def test_invoice_over_order_quantity_is_refused(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    order = owner_client.post(
        f"{COMMERCE}/sales-orders",
        json={
            "party_id": stock_setup["customer"], "order_date": "2026-10-05",
            "lines": [{"product_id": stock_setup["product"], "quantity": "2", "rate": "150.00", "tax_rate": "0"}],
        },
        headers=h,
    )
    assert order.status_code == 201, order.text
    owner_client.post(f"{COMMERCE}/sales-orders/{order.json()['id']}/status", json={"status": "confirmed"}, headers=h)
    so_line = order.json()["lines"][0]["id"]
    response = owner_client.post(
        f"{COMMERCE}/sales-invoices",
        json={
            "sales_order_id": order.json()["id"], "invoice_date": "2026-10-08", "warehouse_id": stock_setup["warehouse"],
            "location_id": stock_setup["location"], "lines": [{"sales_order_line_id": so_line, "quantity": "5"}],
        },
        headers=h,
    )
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------- document processing and access control

def test_processing_job_for_unknown_document_is_not_found(owner_client, books):
    response = owner_client.post(
        f"{COMMERCE}/document-jobs",
        json={"document_id": "00000000-0000-4000-8000-000000000001"},
        headers=csrf_headers(owner_client),
    )
    assert response.status_code == 404, response.text


def test_commerce_mutation_without_csrf_is_refused(owner_client, books):
    response = owner_client.post(f"{COMMERCE}/categories", json={"name": "Hardware"})
    assert response.status_code == 403


def test_commerce_reads_require_authentication(client):
    assert client.get(f"{COMMERCE}/products").status_code == 401
    assert client.get(f"{COMMERCE}/dashboard").status_code == 401
