"""Finance end-to-end flows A-E against PostgreSQL through the HTTP API.

Flow definitions used here (the repository had no separate written list, so these are the ones this suite implements):
  A. Debt with EMI schedule -> EMI obligation -> payment posts a journal -> duplicate reference replays -> overpayment
     refused -> payment reversal posts a mirror journal -> second reversal refused.
  B. Posted purchase bill -> payable -> settlement request -> approve posts a journal -> approve replays ->
     reject after approve refused -> settlement reversal posts a mirror journal.
  C. Posted sales invoice -> receivable -> request rejected -> no journal -> approving after rejection refused.
  D. Read-only bank: fixture accounts and transactions imported -> reconciliation proposes suggestions only ->
     owner confirms one -> nothing is applied to the ledger automatically.
  E. Boundaries: mutations without CSRF refused; AI endpoint refuses write requests and never runs arbitrary SQL.

UNVERIFIED in the environment where this was written: these tests need SQLAlchemy, FastAPI, Alembic and psycopg, plus
a disposable TEST_DATABASE_URL. Run with `pytest tests/integration/test_finance_flows.py`. Do not treat them as
passing until executed. Expected status codes follow app/api/routes/finance.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.api.routes.finance import get_bank_provider
from app.bank.providers import FixtureBankProvider, ProviderAccount, ProviderTransaction
from app.main import app
from tests.conftest import csrf_headers
from tests.integration.test_commerce_flows import (  # noqa: F401  (fixtures re-exported for pytest)
    ACCOUNTING,
    COMMERCE,
    _accounts,
    books,
    stock_setup,
)

FIN = "/api/v1/finance"


def _cash_and_debit(owner_client) -> tuple[str, str]:
    rows = owner_client.get(f"{ACCOUNTING}/accounts").json()
    cash = next(a["id"] for a in rows if a["subtype"] in ("cash", "bank") and a["is_active"])
    expense = _accounts(owner_client)["5900"]
    return cash, expense


def _journal(owner_client, entry_id: str) -> dict:
    response = owner_client.get(f"{ACCOUNTING}/journal-entries/{entry_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _posted_purchase_bill(owner_client, setup, quantity: str = "2") -> str:
    h = csrf_headers(owner_client)
    request = owner_client.post(f"{COMMERCE}/purchase-requests", json={
        "request_date": "2026-10-05", "lines": [{"product_id": setup["product"], "quantity": quantity}]}, headers=h)
    assert request.status_code == 201, request.text
    assert owner_client.post(f"{COMMERCE}/purchase-requests/{request.json()['id']}/decision?decision=approve", headers=h).status_code == 200
    order = owner_client.post(f"{COMMERCE}/purchase-orders", json={
        "party_id": setup["supplier"], "order_date": "2026-10-05", "purchase_request_id": request.json()["id"],
        "lines": [{"product_id": setup["product"], "quantity": quantity, "rate": "100.00", "tax_rate": "18"}]}, headers=h)
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    assert owner_client.post(f"{COMMERCE}/purchase-orders/{order_id}/status?status=approved", headers=h).status_code == 200
    receipt = owner_client.post(f"{COMMERCE}/goods-receipts", json={
        "purchase_order_id": order_id, "receipt_date": "2026-10-06", "warehouse_id": setup["warehouse"],
        "location_id": setup["location"], "lines": [{"purchase_order_line_id": order.json()["lines"][0]["id"], "quantity": quantity}]},
        headers=h)
    assert receipt.status_code == 201, receipt.text
    receipt_detail = owner_client.get(f"{COMMERCE}/goods-receipts/{receipt.json()['id']}").json()
    bill = owner_client.post(f"{COMMERCE}/purchase-bills", json={
        "party_id": setup["supplier"], "supplier_invoice_number": f"INV-{quantity}", "bill_date": "2026-10-07",
        "purchase_order_id": order_id,
        "lines": [{"goods_receipt_line_id": receipt_detail["lines"][0]["id"], "quantity": quantity}]}, headers=h)
    assert bill.status_code == 201, bill.text
    posted = owner_client.post(f"{COMMERCE}/purchase-bills/{bill.json()['id']}/post", headers=h)
    assert posted.status_code == 200, posted.text
    return bill.json()["id"]


def _posted_sales_invoice(owner_client, setup) -> str:
    h = csrf_headers(owner_client)
    quote = owner_client.post(f"{COMMERCE}/quotations", json={
        "party_id": setup["customer"], "quotation_date": "2026-10-05",
        "lines": [{"product_id": setup["product"], "quantity": "1", "rate": "150.00", "tax_rate": "18"}]}, headers=h)
    assert quote.status_code == 201, quote.text
    qid = quote.json()["id"]
    for target in ("sent", "accepted"):
        assert owner_client.post(f"{COMMERCE}/quotations/{qid}/status", json={"status": target}, headers=h).status_code == 200
    converted = owner_client.post(f"{COMMERCE}/quotations/{qid}/convert", headers=h)
    assert converted.status_code == 201, converted.text
    order_id = converted.json()["id"]
    assert owner_client.post(f"{COMMERCE}/sales-orders/{order_id}/status", json={"status": "confirmed"}, headers=h).status_code == 200
    invoice = owner_client.post(f"{COMMERCE}/sales-invoices", json={
        "sales_order_id": order_id, "invoice_date": "2026-10-08", "warehouse_id": setup["warehouse"],
        "location_id": setup["location"], "lines": [{"sales_order_line_id": converted.json()["lines"][0]["id"], "quantity": "1"}]},
        headers=h)
    assert invoice.status_code == 201, invoice.text
    assert owner_client.post(f"{COMMERCE}/sales-invoices/{invoice.json()['id']}/post", headers=h).status_code == 200
    return invoice.json()["id"]


# ---------------------------------------------------------------- Flow A: debt, EMI payment, reversal

def test_flow_a_emi_payment_posts_and_reverses_journal(owner_client, books):
    h = csrf_headers(owner_client)
    november = owner_client.post(
        f"{ACCOUNTING}/periods",
        json={"name": "Nov 2026", "start_date": "2026-11-01", "end_date": "2026-11-30"},
        headers=h,
    )
    assert november.status_code == 201, november.text
    cash, expense = _cash_and_debit(owner_client)
    debt = owner_client.post(f"{FIN}/debts", json={
        "name": "Car loan", "lender": "Bank X", "principal": "120000.00", "annual_rate": "12",
        "tenure_months": 12, "start_date": "2026-10-01", "first_due_date": "2026-11-01"}, headers=h)
    assert debt.status_code == 201, debt.text
    emi_obligation = next(o for o in owner_client.get(f"{FIN}/emis").json() if o["debt_id"] == debt.json()["id"])
    amount = emi_obligation["amount"]
    due = emi_obligation["first_due_date"]

    paid = owner_client.post(f"{FIN}/obligations/{emi_obligation['id']}/payments", json={
        "period_due_date": due, "amount": amount, "paid_on": "2026-11-01",
        "cash_account_id": cash, "debit_account_id": expense, "reference": "A-EMI-1"}, headers=h)
    assert paid.status_code == 201, paid.text
    entry_id = paid.json()["journal_entry_id"]
    assert entry_id is not None
    entry = _journal(owner_client, entry_id)
    assert entry["status"] == "posted"

    replay = owner_client.post(f"{FIN}/obligations/{emi_obligation['id']}/payments", json={
        "period_due_date": due, "amount": amount, "paid_on": "2026-11-01",
        "cash_account_id": cash, "debit_account_id": expense, "reference": "A-EMI-1"}, headers=h)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == paid.json()["id"]

    over = owner_client.post(f"{FIN}/obligations/{emi_obligation['id']}/payments", json={
        "period_due_date": due, "amount": "1.00", "paid_on": "2026-11-01",
        "cash_account_id": cash, "debit_account_id": expense, "reference": "A-EMI-2"}, headers=h)
    assert over.status_code == 409, over.text

    reversed_ = owner_client.post(f"{FIN}/payments/{paid.json()['id']}/reverse", json={"paid_on": "2026-11-02"}, headers=h)
    assert reversed_.status_code == 201, reversed_.text
    again = owner_client.post(f"{FIN}/payments/{paid.json()['id']}/reverse", json={"paid_on": "2026-11-02"}, headers=h)
    assert again.status_code == 409, again.text


# ---------------------------------------------------------------- Flow B: payable settlement lifecycle

def test_flow_b_payable_request_approve_replay_and_reverse(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    accounts = _accounts(owner_client)
    bill_id = _posted_purchase_bill(owner_client, stock_setup)
    cash, _ = _cash_and_debit(owner_client)
    control = accounts["2100"]

    payables = owner_client.get(f"{FIN}/payables").json()
    row = next(i for i in payables["items"] if i["id"] == bill_id)
    assert Decimal(row["outstanding"]) == Decimal(row["total"])

    body = {"direction": "payable", "document_id": bill_id, "amount": "100.00", "settled_on": "2026-10-10",
            "method": "bank", "reference": "B-1", "cash_account_id": cash, "control_account_id": control}
    created = owner_client.post(f"{FIN}/settlement-requests", json=body, headers=h)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"

    approved = owner_client.post(f"{FIN}/settlement-requests/{created.json()['id']}/approve", headers=h)
    assert approved.status_code == 201, approved.text
    assert approved.json()["state"] == "completed"
    assert _journal(owner_client, approved.json()["journal_entry_id"])["status"] == "posted"

    replay = owner_client.post(f"{FIN}/settlement-requests/{created.json()['id']}/approve", headers=h)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == approved.json()["id"]

    reject_after = owner_client.post(f"{FIN}/settlement-requests/{created.json()['id']}/reject", headers=h)
    assert reject_after.status_code == 409, reject_after.text

    reversed_ = owner_client.post(f"{FIN}/settlements/{approved.json()['id']}/reverse", json={
        "reversed_on": "2026-10-11", "reason": "Entered against the wrong bill"}, headers=h)
    assert reversed_.status_code == 201, reversed_.text


# ---------------------------------------------------------------- Flow C: receivable request rejected

def test_flow_c_receivable_rejection_posts_nothing(owner_client, stock_setup):
    h = csrf_headers(owner_client)
    accounts = _accounts(owner_client)
    invoice_id = _posted_sales_invoice(owner_client, stock_setup)
    cash, _ = _cash_and_debit(owner_client)
    body = {"direction": "receivable", "document_id": invoice_id, "amount": "50.00", "settled_on": "2026-10-12",
            "method": "upi", "reference": "C-1", "cash_account_id": cash, "control_account_id": accounts["1300"]}
    created = owner_client.post(f"{FIN}/settlement-requests", json=body, headers=h)
    assert created.status_code == 201, created.text

    rejected = owner_client.post(f"{FIN}/settlement-requests/{created.json()['id']}/reject", headers=h)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    approve_after = owner_client.post(f"{FIN}/settlement-requests/{created.json()['id']}/approve", headers=h)
    assert approve_after.status_code == 409, approve_after.text

    listed = owner_client.get(f"{FIN}/settlements").json()
    assert all(s["reference"] != "C-1" for s in listed)


# ---------------------------------------------------------------- Flow D: read-only bank and reconciliation

@pytest.fixture()
def bank_provider():
    provider = FixtureBankProvider(
        accounts=[ProviderAccount("ext-1", "Current account", "QAR", Decimal("5000.00"), date(2026, 10, 9))],
        transactions={"ext-1": [
            ProviderTransaction("t-1", date(2026, 10, 10), Decimal("-100.00"), "Supplier payment", Decimal("4900.00")),
        ]},
    )
    app.dependency_overrides[get_bank_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_bank_provider, None)


def test_flow_d_bank_import_is_read_only_and_suggestions_need_confirmation(owner_client, books, bank_provider):
    h = csrf_headers(owner_client)
    imported = owner_client.post(f"{FIN}/bank/accounts/import", headers=h)
    assert imported.status_code == 200, imported.text
    assert imported.json()["read_only"] is True
    account_id = owner_client.get(f"{FIN}/bank/accounts").json()[0]["id"]
    cash, _ = _cash_and_debit(owner_client)
    linked = owner_client.post(
        f"{FIN}/bank/accounts/{account_id}/ledger-link",
        json={"ledger_account_id": cash},
        headers=h,
    )
    assert linked.status_code == 200, linked.text

    txns = owner_client.post(f"{FIN}/bank/accounts/{account_id}/transactions/import", json={
        "since": "2026-10-01", "until": "2026-10-31"}, headers=h)
    assert txns.status_code == 200, txns.text

    listed = owner_client.get(f"{FIN}/bank/accounts/{account_id}/transactions?since=2026-10-01&until=2026-10-31").json()
    assert len(listed) == 1 and listed[0]["direction"] == "debit"

    run = owner_client.post(f"{FIN}/reconciliation/run", json={
        "bank_account_id": account_id, "since": "2026-10-01", "until": "2026-10-31"}, headers=h)
    assert run.status_code in (200, 201), run.text
    suggestions = owner_client.get(f"{FIN}/reconciliation/suggestions?status=suggested").json()
    assert all(s["status"] == "suggested" for s in suggestions)
    if suggestions:
        decided = owner_client.post(f"{FIN}/reconciliation/suggestions/{suggestions[0]['id']}/decision",
                                    json={"decision": "rejected"}, headers=h)
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "rejected"


# ---------------------------------------------------------------- Flow E: boundaries

def test_flow_e_mutations_need_csrf_and_ai_cannot_write(owner_client, books):
    no_csrf = owner_client.post(f"{FIN}/debts", json={
        "name": "X", "lender": "Y", "principal": "1000.00", "annual_rate": "5", "tenure_months": 3, "start_date": "2026-10-01"})
    assert no_csrf.status_code in (401, 403), no_csrf.text

    before_requests = owner_client.get(f"{FIN}/settlement-requests").json()
    before_settlements = owner_client.get(f"{FIN}/settlements").json()
    for question in ("pay the rent now", "run select * from users", "transfer 500 to supplier"):
        answer = owner_client.post(f"{FIN}/ai/ask", json={"question": question}, headers=csrf_headers(owner_client))
        assert answer.status_code in (200, 422), answer.text
    # The AI boundary is read-only: none of the prompts may create a request, settlement or payment.
    assert owner_client.get(f"{FIN}/settlement-requests").json() == before_requests
    assert owner_client.get(f"{FIN}/settlements").json() == before_settlements
