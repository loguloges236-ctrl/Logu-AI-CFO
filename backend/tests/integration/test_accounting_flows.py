"""Accounting API flows against PostgreSQL.

UNVERIFIED in the environment where Phase 2 was written: PostgreSQL, SQLAlchemy and FastAPI were
not installable there. These tests are written to run with `pytest tests/integration` and a
disposable TEST_DATABASE_URL. Do not treat them as passing until they have been executed.
"""

from __future__ import annotations

import pytest

from tests.conftest import csrf_headers

API = "/api/v1/accounting"


def _codes(client) -> dict[str, str]:
    response = client.get(f"{API}/accounts")
    assert response.status_code == 200, response.text
    return {account["code"]: account["id"] for account in response.json()}


@pytest.fixture()
def chart(owner_client):
    created = owner_client.post(f"{API}/setup", headers=csrf_headers(owner_client))
    assert created.status_code == 201, created.text
    return _codes(owner_client)


@pytest.fixture()
def october(owner_client, chart):
    response = owner_client.post(
        f"{API}/periods",
        json={"name": "Oct 2026", "start_date": "2026-10-01", "end_date": "2026-10-31"},
        headers=csrf_headers(owner_client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _line(account_id: str, debit: str = "0", credit: str = "0") -> dict:
    return {"account_id": account_id, "debit": debit, "credit": credit}


def _draft(client, chart, lines, transaction_date="2026-10-05", description="Test entry"):
    return client.post(
        f"{API}/journal-entries",
        json={"transaction_date": transaction_date, "description": description, "lines": lines},
        headers=csrf_headers(client),
    )


def test_default_chart_can_be_set_up_only_once(owner_client):
    first = owner_client.post(f"{API}/setup", headers=csrf_headers(owner_client))
    second = owner_client.post(f"{API}/setup", headers=csrf_headers(owner_client))
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_initialized"


def test_duplicate_account_code_is_rejected(owner_client, chart):
    body = {"code": "1150", "name": "Till", "category": "asset", "subtype": "cash"}
    assert owner_client.post(f"{API}/accounts", json=body, headers=csrf_headers(owner_client)).status_code == 201
    again = owner_client.post(f"{API}/accounts", json=body, headers=csrf_headers(owner_client))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "duplicate_code"


def test_child_must_match_parent_category(owner_client, chart):
    body = {
        "code": "1190",
        "name": "Wrong",
        "category": "expense",
        "subtype": "operating_expense",
        "parent_id": chart["1000"],
    }
    response = owner_client.post(f"{API}/accounts", json=body, headers=csrf_headers(owner_client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "parent_category_mismatch"


def test_unbalanced_draft_is_saved_but_cannot_post(owner_client, chart, october):
    draft = _draft(owner_client, chart, [_line(chart["1100"], "100.00"), _line(chart["4100"], credit="99.99")])
    assert draft.status_code == 201, draft.text
    entry_id = draft.json()["id"]
    posted = owner_client.post(f"{API}/journal-entries/{entry_id}/post", headers=csrf_headers(owner_client))
    assert posted.status_code == 422
    assert posted.json()["error"]["code"] == "unbalanced_entry"


def test_balanced_entry_posts_and_becomes_immutable(owner_client, chart, october):
    draft = _draft(owner_client, chart, [_line(chart["1100"], "250.50"), _line(chart["4100"], credit="250.50")])
    entry_id = draft.json()["id"]
    posted = owner_client.post(f"{API}/journal-entries/{entry_id}/post", headers=csrf_headers(owner_client))
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["status"] == "posted"
    assert body["entry_number"] is not None
    assert body["posting_date"] == "2026-10-05"

    edit = owner_client.put(
        f"{API}/journal-entries/{entry_id}",
        json={"transaction_date": "2026-10-05", "description": "changed", "lines": []},
        headers=csrf_headers(owner_client),
    )
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "not_draft"

    delete = owner_client.delete(f"{API}/journal-entries/{entry_id}", headers=csrf_headers(owner_client))
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "not_draft"


def test_reversal_nets_to_zero_and_cannot_repeat(owner_client, chart, october):
    draft = _draft(owner_client, chart, [_line(chart["5100"], "500.00"), _line(chart["1200"], credit="500.00")])
    entry_id = draft.json()["id"]
    owner_client.post(f"{API}/journal-entries/{entry_id}/post", headers=csrf_headers(owner_client))

    reversed_ = owner_client.post(
        f"{API}/journal-entries/{entry_id}/reverse",
        json={"posting_date": "2026-10-06"},
        headers=csrf_headers(owner_client),
    )
    assert reversed_.status_code == 201, reversed_.text
    assert reversed_.json()["reverses_entry_id"] == entry_id

    again = owner_client.post(
        f"{API}/journal-entries/{entry_id}/reverse",
        json={"posting_date": "2026-10-06"},
        headers=csrf_headers(owner_client),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_reversed"


def test_posting_into_closed_period_is_rejected(owner_client, chart, october):
    closed = owner_client.post(f"{API}/periods/{october['id']}/close", headers=csrf_headers(owner_client))
    assert closed.status_code == 200, closed.text
    draft = _draft(owner_client, chart, [_line(chart["1100"], "10.00"), _line(chart["4100"], credit="10.00")])
    posted = owner_client.post(
        f"{API}/journal-entries/{draft.json()['id']}/post", headers=csrf_headers(owner_client)
    )
    assert posted.status_code == 409
    assert posted.json()["error"]["code"] == "no_open_period"


def test_overlapping_period_is_rejected(owner_client, october):
    response = owner_client.post(
        f"{API}/periods",
        json={"name": "Mid Oct", "start_date": "2026-10-15", "end_date": "2026-11-15"},
        headers=csrf_headers(owner_client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "period_overlap"


def test_posting_to_header_account_is_rejected(owner_client, chart, october):
    draft = _draft(owner_client, chart, [_line(chart["1000"], "5.00"), _line(chart["4100"], credit="5.00")])
    posted = owner_client.post(
        f"{API}/journal-entries/{draft.json()['id']}/post", headers=csrf_headers(owner_client)
    )
    assert posted.status_code == 422
    assert posted.json()["error"]["code"] == "header_account"


def test_trial_balance_balances_and_ledger_runs(owner_client, chart, october):
    entries = [
        [_line(chart["1100"], "1000.00"), _line(chart["3000"], credit="1000.00")],
        [_line(chart["5100"], "300.00"), _line(chart["1100"], credit="300.00")],
    ]
    for lines in entries:
        draft = _draft(owner_client, chart, lines)
        owner_client.post(f"{API}/journal-entries/{draft.json()['id']}/post", headers=csrf_headers(owner_client))

    tb = owner_client.get(f"{API}/trial-balance", params={"start": "2026-10-01", "end": "2026-10-31"})
    assert tb.status_code == 200, tb.text
    assert tb.json()["is_balanced"] is True
    assert tb.json()["total_debit"] == tb.json()["total_credit"] == "1000.00"

    ledger = owner_client.get(f"{API}/accounts/{chart['1100']}/ledger")
    assert ledger.status_code == 200, ledger.text
    assert [row["balance"] for row in ledger.json()["rows"]] == ["1000.00", "700.00"]


def test_profit_and_loss_and_balance_sheet(owner_client, chart, october):
    draft = _draft(owner_client, chart, [_line(chart["1100"], "900.00"), _line(chart["4100"], credit="900.00")])
    owner_client.post(f"{API}/journal-entries/{draft.json()['id']}/post", headers=csrf_headers(owner_client))
    pnl = owner_client.get(f"{API}/reports/profit-and-loss", params={"start": "2026-10-01", "end": "2026-10-31"})
    assert pnl.json()["net_profit"] == "900.00"
    sheet = owner_client.get(f"{API}/reports/balance-sheet", params={"as_of": "2026-10-31"})
    assert sheet.json()["is_balanced"] is True


def test_mutation_without_csrf_is_refused(owner_client, chart):
    response = owner_client.post(
        f"{API}/accounts",
        json={"code": "1199", "name": "X", "category": "asset", "subtype": "general"},
    )
    assert response.status_code == 403


def test_accounting_requires_authentication(client):
    response = client.get(f"{API}/accounts")
    assert response.status_code == 401


def test_float_amount_over_the_api_is_rejected(owner_client, chart, october):
    response = _draft(owner_client, chart, [_line(chart["1100"], 10.5), _line(chart["4100"], credit="10.50")])
    assert response.status_code == 422
