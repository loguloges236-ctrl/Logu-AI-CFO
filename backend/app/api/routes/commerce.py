"""Authenticated Phase 3 API: catalogue, stock, purchasing, sales, document processing and the dashboard.

Every route requires the owner session. Every mutation also requires the CSRF token. There are no public routes,
no raw SQL and no filters that accept expressions. Money and quantities go out as decimal strings.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.accounting.domain import AccountingRuleViolation
from app.api.deps import get_cipher, get_storage
from app.commerce import domain as cd
from app.commerce.extraction import DocumentExtractionProvider, UnavailableExtractionProvider
from app.core.errors import APIError
from app.db.session import get_db
from app.models.commerce import (
    CommerceAccountMapping,
    DocumentProcessingJob,
    GoodsReceipt,
    Product,
    PurchaseBill,
    PurchaseBillLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequest,
    PurchaseRequestLine,
    Quotation,
    QuotationLine,
    SalesInvoice,
    SalesInvoiceLine,
    SalesOrder,
    SalesOrderLine,
    StockLocation,
    StockMovement,
    Warehouse,
)
from app.models.entities import DocumentFile, FileScanStatus
from app.schemas import commerce as sc
from app.security.crypto import SensitiveDataCipher
from app.services import commerce_catalog as catalog
from app.services import commerce_documents as documents
from app.services import commerce_inventory as inventory
from app.services import commerce_purchasing as purchasing
from app.services import commerce_reports as reports
from app.services import commerce_sales as sales
from app.services.auth import AuthenticatedSession, get_current_auth, require_csrf
from app.services.commerce_common import CommerceRuleViolation
from app.services.documents import get_owner_document, read_for_download
from app.storage.local import LocalFileStorage

router = APIRouter(prefix="/commerce", tags=["commerce"])

_NOT_FOUND = "not_found"
_CONFLICT_CODES = frozenset(
    {
        "duplicate_sku", "duplicate_barcode", "duplicate_category", "duplicate_code", "duplicate_supplier_invoice",
        "inactive_product", "inactive_party", "negative_stock", "stock_unavailable", "over_receipt",
        "bill_price_mismatch", "over_invoice", "not_draft", "not_posted", "po_has_receipts", "po_not_receivable",
        "order_has_invoices", "order_not_invoiceable", "job_exists", "job_closed", "job_not_runnable",
        "uom_locked", "sku_immutable", "stock_type_locked", "purchase_request_not_approved", "invalid_transition",
        "database_rule", "requires_goods_receipt", "document_not_available", "already_posted", "already_reversed",
        "no_open_period", "accounting_not_configured",
    }
)


@contextmanager
def _translate() -> Iterator[None]:
    """Stable, client-safe errors for domain and accounting rule failures."""
    try:
        yield
    except CommerceRuleViolation as error:
        if error.code == _NOT_FOUND:
            raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND, error.message) from None
        http_status = status.HTTP_409_CONFLICT if error.code in _CONFLICT_CODES else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise APIError(http_status, error.code, error.message) from None
    except AccountingRuleViolation as error:
        if error.code == _NOT_FOUND:
            raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND, error.message) from None
        http_status = status.HTTP_409_CONFLICT if error.code in _CONFLICT_CODES else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise APIError(http_status, error.code, error.message) from None


def get_extraction_provider() -> DocumentExtractionProvider:
    """No OCR or document AI is bundled. Tests and future adapters override this dependency."""
    return UnavailableExtractionProvider()


# ---------------------------------------------------------------- serialisers (decimals become strings)


def _m(value: Any) -> str | None:
    return None if value is None else cd.money(Decimal(value))


def _q(value: Any) -> str:
    return cd.quantity_text(Decimal(value))


def _c(value: Any) -> str | None:
    return None if value is None else cd.cost_text(Decimal(value))


def _plain(value: Any) -> str | None:
    return None if value is None else str(Decimal(value))


def _product(p: Product) -> dict[str, Any]:
    return {
        "id": p.id, "sku": p.sku, "barcode": p.barcode, "name": p.name, "description": p.description,
        "category_id": p.category_id, "unit_of_measure": p.unit_of_measure, "hsn_sac": p.hsn_sac,
        "tax_rate": _plain(p.tax_rate), "purchase_price": _m(p.purchase_price), "selling_price": _m(p.selling_price),
        "is_stock_item": p.is_stock_item, "is_active": p.is_active,
    }


def _party(p: Any) -> dict[str, Any]:
    return {"id": p.id, "kind": p.kind, "name": p.name, "tax_id": p.tax_id, "email": p.email, "phone": p.phone, "is_active": p.is_active}


def _warehouse(w: Warehouse) -> dict[str, Any]:
    return {"id": w.id, "code": w.code, "name": w.name, "allow_negative_stock": w.allow_negative_stock, "is_active": w.is_active}


def _location(loc: StockLocation) -> dict[str, Any]:
    return {"id": loc.id, "warehouse_id": loc.warehouse_id, "code": loc.code, "name": loc.name, "is_active": loc.is_active}


def _movement(mv: StockMovement, sku: str) -> dict[str, Any]:
    return {
        "id": mv.id, "seq": mv.seq, "sku": sku, "product_id": mv.product_id, "location_id": mv.location_id,
        "movement_type": mv.movement_type, "quantity_delta": _q(mv.quantity_delta), "unit_cost": _c(mv.unit_cost),
        "balance_after": _q(mv.balance_after), "average_cost_after": _c(mv.average_cost_after),
        "source_type": mv.source_type, "source_id": mv.source_id, "transfer_group_id": mv.transfer_group_id,
        "reason": mv.reason, "created_at": mv.created_at,
    }


def _header_totals(h: Any) -> dict[str, Any]:
    return {
        "subtotal": _m(h.subtotal), "discount_total": _m(h.discount_total), "taxable_total": _m(h.taxable_total),
        "tax_total": _m(h.tax_total), "grand_total": _m(h.grand_total),
    }


def _line(line: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "id": line.id, "line_number": line.line_number, "product_id": line.product_id, "description": line.description,
        "quantity": _q(line.quantity), "rate": _c(line.rate), "discount_percent": _plain(line.discount_percent),
        "tax_rate": _plain(line.tax_rate), "line_subtotal": _m(line.line_subtotal), "line_discount": _m(line.line_discount),
        "taxable_amount": _m(line.taxable_amount), "tax_amount": _m(line.tax_amount), "line_total": _m(line.line_total),
    }
    base.update(extra or {})
    return base


def _doc_base(h: Any, number_field: str, date_field: str) -> dict[str, Any]:
    return {
        "id": h.id, "number": getattr(h, number_field), "date": getattr(h, date_field), "status": h.status,
        "created_at": h.created_at, **_header_totals(h),
    }


def _pr_out(h: PurchaseRequest, lines: list[PurchaseRequestLine]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "request_date": h.request_date, "status": h.status,
        "requested_by": h.requested_by, "notes": h.notes,
        "lines": [{"id": x.id, "line_number": x.line_number, "product_id": x.product_id, "description": x.description, "quantity": _q(x.quantity)} for x in lines],
    }


def _po_out(h: PurchaseOrder, lines: list[PurchaseOrderLine], summary: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "order_date": h.order_date, "expected_date": h.expected_date,
        "party_id": h.party_id, "purchase_request_id": h.purchase_request_id, "status": h.status, "notes": h.notes,
        **_header_totals(h),
        "lines": [_line(x) for x in lines],
        "receipt_summary": [
            {**row, "purchase_order_line_id": row["purchase_order_line_id"], "ordered": _q(row["ordered"]),
             "received": _q(row["received"]), "billed": _q(row["billed"])}
            for row in summary
        ],
    }


def _bill_out(h: PurchaseBill, lines: list[PurchaseBillLine]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "supplier_invoice_number": h.supplier_invoice_number,
        "bill_date": h.bill_date, "due_date": h.due_date, "party_id": h.party_id,
        "purchase_order_id": h.purchase_order_id, "status": h.status, "notes": h.notes,
        "payment_reference": h.payment_reference, "payment_date": h.payment_date,
        "journal_entry_id": h.journal_entry_id, "reversal_entry_id": h.reversal_entry_id, "voided_at": h.voided_at,
        **_header_totals(h),
        "lines": [
            _line(x, {"goods_receipt_line_id": x.goods_receipt_line_id, "expense_account_id": x.expense_account_id})
            for x in lines
        ],
    }


def _quote_out(h: Quotation, lines: list[QuotationLine]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "quotation_date": h.quotation_date, "valid_until": h.valid_until,
        "party_id": h.party_id, "status": h.status, "notes": h.notes, **_header_totals(h),
        "lines": [_line(x) for x in lines],
    }


def _so_out(h: SalesOrder, lines: list[SalesOrderLine]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "order_date": h.order_date, "party_id": h.party_id,
        "quotation_id": h.quotation_id, "status": h.status, "notes": h.notes, **_header_totals(h),
        "lines": [
            _line(x, {"invoiced_quantity": _q(x.invoiced_quantity), "open_quantity": _q(x.quantity - x.invoiced_quantity)})
            for x in lines
        ],
    }


def _invoice_out(h: SalesInvoice, lines: list[SalesInvoiceLine]) -> dict[str, Any]:
    return {
        "id": h.id, "number": h.number, "invoice_date": h.invoice_date, "due_date": h.due_date,
        "party_id": h.party_id, "sales_order_id": h.sales_order_id, "warehouse_id": h.warehouse_id,
        "location_id": h.location_id, "status": h.status, "notes": h.notes,
        "cost_of_goods_total": _m(h.cost_of_goods_total), "payment_reference": h.payment_reference,
        "payment_received_on": h.payment_received_on, "journal_entry_id": h.journal_entry_id,
        "reversal_entry_id": h.reversal_entry_id, "voided_at": h.voided_at, **_header_totals(h),
        "lines": [
            _line(x, {"sales_order_line_id": x.sales_order_line_id, "unit_cost": _c(x.unit_cost), "cost_amount": _m(x.cost_amount)})
            for x in lines
        ],
    }


def _job_out(job: DocumentProcessingJob, detail: bool = False) -> dict[str, Any]:
    data = documents.job_summary(job)
    data["classification_confidence"] = _plain(job.classification_confidence)
    if detail:
        data["current_payload"] = job.current_payload
        data["field_confidence"] = job.field_confidence
        data["raw_extraction"] = job.raw_extraction
        data["review_acknowledged"] = job.review_acknowledged
    return data


def _lines_payload(lines: list[sc.DocLineIn]) -> list[dict[str, Any]]:
    return [line.model_dump(mode="python") for line in lines]


# ---------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    return reports.dashboard(db)


# ---------------------------------------------------------------- categories, products, parties


@router.get("/categories")
def list_categories(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"id": c.id, "name": c.name, "description": c.description} for c in catalog.list_categories(db)]


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    body: sc.CategoryIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    with _translate():
        c = catalog.create_category(db, request, auth, body.name, body.description)
    return {"id": c.id, "name": c.name, "description": c.description}


@router.get("/products")
def list_products(
    search: str | None = Query(default=None, max_length=80),
    active_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    rows, total = catalog.list_products(db, search=search, active_only=active_only, limit=limit, offset=offset)
    return {"items": [_product(p) for p in rows], "total": total}


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    body: sc.ProductIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    with _translate():
        product = catalog.create_product(db, request, auth, **body.model_dump())
    return _product(product)


@router.get("/products/{product_id}")
def get_product(product_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return _product(catalog.get_product(db, product_id))


@router.patch("/products/{product_id}")
def update_product(
    product_id: UUID, body: sc.ProductUpdate, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    with _translate():
        return _product(catalog.update_product(db, request, auth, product_id, changes))


@router.get("/parties")
def list_parties(
    kind: str | None = Query(default=None, pattern="^(supplier|customer)$"),
    active_only: bool = False,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [_party(p) for p in catalog.list_parties(db, kind, active_only)]


@router.post("/parties", status_code=status.HTTP_201_CREATED)
def create_party(
    body: sc.PartyIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    with _translate():
        return _party(catalog.create_party(db, request, auth, **body.model_dump()))


@router.patch("/parties/{party_id}")
def update_party(
    party_id: UUID, body: sc.PartyUpdate, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        return _party(catalog.update_party(db, request, auth, party_id, body.model_dump(exclude_unset=True)))


# ---------------------------------------------------------------- warehouses, locations, accounting roles


@router.get("/warehouses")
def list_warehouses(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [_warehouse(w) for w in catalog.list_warehouses(db)]


@router.post("/warehouses", status_code=status.HTTP_201_CREATED)
def create_warehouse(
    body: sc.WarehouseIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    with _translate():
        return _warehouse(catalog.create_warehouse(db, request, auth, **body.model_dump()))


@router.patch("/warehouses/{warehouse_id}")
def update_warehouse(
    warehouse_id: UUID, body: sc.WarehouseUpdate, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        return _warehouse(catalog.update_warehouse(db, request, auth, warehouse_id, body.model_dump(exclude_unset=True)))


@router.get("/locations")
def list_locations(
    warehouse_id: UUID | None = None, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)
) -> list[dict[str, Any]]:
    return [_location(loc) for loc in catalog.list_locations(db, warehouse_id)]


@router.post("/locations", status_code=status.HTTP_201_CREATED)
def create_location(
    body: sc.LocationIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    with _translate():
        return _location(catalog.create_location(db, request, auth, **body.model_dump()))


@router.patch("/locations/{location_id}")
def update_location(
    location_id: UUID, body: sc.LocationUpdate, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        return _location(catalog.update_location(db, request, auth, location_id, body.model_dump(exclude_unset=True)))


@router.get("/accounting-roles")
def list_mappings(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"role": m.role, "account_id": m.account_id, "updated_at": m.updated_at} for m in catalog.list_mappings(db)]


@router.put("/accounting-roles/{role}")
def set_mapping(
    role: str, body: sc.MappingIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        m: CommerceAccountMapping = catalog.set_mapping(db, request, auth, role, body.account_id)
    return {"role": m.role, "account_id": m.account_id, "updated_at": m.updated_at}


# ---------------------------------------------------------------- stock


@router.get("/stock/balances")
def stock_balances(
    warehouse_id: UUID | None = None, product_id: UUID | None = None, non_zero: bool = False,
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = inventory.stock_balances(db, warehouse_id=warehouse_id, product_id=product_id, non_zero_only=non_zero)
    return [
        {**row, "product_id": row["product_id"], "quantity": _q(row["quantity"]),
         "average_cost": _c(row["average_cost"]), "value": _m(row["value"])}
        for row in rows
    ]


@router.get("/stock/valuation")
def stock_valuation(
    warehouse_id: UUID | None = None, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)
) -> dict[str, Any]:
    data = inventory.valuation(db, warehouse_id=warehouse_id)
    rows = [{**row, "quantity": _q(row["quantity"]), "average_cost": _c(row["average_cost"]), "value": _m(row["value"])} for row in data["rows"]]
    return {"rows": rows, "total_value": _m(data["total_value"])}


@router.get("/stock/movements")
def stock_movements(
    product_id: UUID | None = None,
    location_id: UUID | None = None,
    movement_type: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    rows, total = inventory.list_movements(
        db, product_id=product_id, location_id=location_id, movement_type=movement_type, limit=limit, offset=offset
    )
    return {"items": [_movement(mv, sku.sku) for mv, sku in rows], "total": total}


@router.get("/stock/integrity")
def stock_integrity(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    problems = inventory.integrity_check(db)
    return {"ok": not problems, "mismatches": problems}


@router.post("/stock/receive", status_code=status.HTTP_201_CREATED)
def receive_stock(body: sc.StockInIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.receive_stock(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/issue", status_code=status.HTTP_201_CREATED)
def issue_stock(body: sc.StockOutIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.issue_stock(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/damage", status_code=status.HTTP_201_CREATED)
def damage_stock(body: sc.StockOutIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.damage_stock(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/return-to-supplier", status_code=status.HTTP_201_CREATED)
def return_to_supplier(body: sc.StockOutIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.return_to_supplier(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/customer-return", status_code=status.HTTP_201_CREATED)
def customer_return(body: sc.CustomerReturnIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.customer_return(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/adjust", status_code=status.HTTP_201_CREATED)
def adjust_stock(body: sc.AdjustmentIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        mv = inventory.adjust_stock(db, request, auth, **body.model_dump())
        return _movement(mv, catalog.get_product(db, mv.product_id).sku)


@router.post("/stock/transfer", status_code=status.HTTP_201_CREATED)
def transfer_stock(body: sc.TransferIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        out, into = inventory.transfer_stock(db, request, auth, **body.model_dump())
        sku = catalog.get_product(db, out.product_id).sku
        return {"transfer_group_id": out.transfer_group_id, "legs": [_movement(out, sku), _movement(into, sku)]}


@router.post("/stock/reconcile")
def reconcile_stock(body: sc.ReconcileIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return inventory.reconcile_count(db, request, auth, **body.model_dump())


# ---------------------------------------------------------------- purchase requests


@router.get("/purchase-requests")
def list_purchase_requests(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": r.id, "number": r.number, "request_date": r.request_date, "status": r.status, "requested_by": r.requested_by}
        for r in purchasing.list_purchase_requests(db, status_filter)
    ]


@router.post("/purchase-requests", status_code=status.HTTP_201_CREATED)
def create_purchase_request(body: sc.PurchaseRequestIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        h = purchasing.create_purchase_request(
            db, request, auth, request_date=body.request_date, requested_by=body.requested_by,
            notes=body.notes, lines=_lines_payload(body.lines),
        )
    return _pr_out(h, _pr_lines(db, h.id))


def _pr_lines(db: DBSession, request_id: UUID) -> list[PurchaseRequestLine]:
    return list(db.scalars(select(PurchaseRequestLine).where(PurchaseRequestLine.purchase_request_id == request_id).order_by(PurchaseRequestLine.line_number)))


@router.get("/purchase-requests/{request_id}")
def get_purchase_request(request_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        h = purchasing.get_purchase_request(db, request_id)
        return _pr_out(h, _pr_lines(db, h.id))


@router.post("/purchase-requests/{request_id}/decision")
def decide_purchase_request(
    request_id: UUID, request: Request, decision: str = Query(pattern="^(approve|reject)$"),
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        h = purchasing.decide_purchase_request(db, request, auth, request_id, decision)
    return {"id": h.id, "status": h.status}


@router.delete("/purchase-requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_purchase_request(request_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        purchasing.discard_purchase_request(db, request, auth, request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- purchase orders


@router.get("/purchase-orders")
def list_purchase_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": o.id, "number": o.number, "order_date": o.order_date, "status": o.status, "party_id": o.party_id, **_header_totals(o)}
        for o in purchasing.list_purchase_orders(db, status_filter)
    ]


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
def create_purchase_order(body: sc.PurchaseOrderIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        h = purchasing.create_purchase_order(
            db, request, auth, party_id=body.party_id, order_date=body.order_date, expected_date=body.expected_date,
            purchase_request_id=body.purchase_request_id, notes=body.notes, lines=_lines_payload(body.lines),
        )
        return _po_out(h, purchasing.purchase_order_lines(db, h.id), purchasing.receipt_summary(db, h.id))


@router.get("/purchase-orders/{order_id}")
def get_purchase_order(order_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        h = purchasing.get_purchase_order(db, order_id)
        return _po_out(h, purchasing.purchase_order_lines(db, h.id), purchasing.receipt_summary(db, h.id))


@router.post("/purchase-orders/{order_id}/status")
def set_purchase_order_status(
    order_id: UUID, request: Request, target: str = Query(alias="status", pattern="^(approved|cancelled)$"),
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        h = purchasing.transition_purchase_order(db, request, auth, order_id, target)
    return {"id": h.id, "status": h.status}


@router.delete("/purchase-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_purchase_order(order_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        purchasing.discard_purchase_order(db, request, auth, order_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- goods receipts


@router.get("/goods-receipts")
def list_goods_receipts(
    purchase_order_id: UUID | None = None, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        {"id": g.id, "number": g.number, "receipt_date": g.receipt_date, "purchase_order_id": g.purchase_order_id, "status": g.status}
        for g in purchasing.list_goods_receipts(db, purchase_order_id)
    ]


@router.post("/goods-receipts", status_code=status.HTTP_201_CREATED)
def create_goods_receipt(body: sc.GoodsReceiptIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        g = purchasing.create_goods_receipt(
            db, request, auth, purchase_order_id=body.purchase_order_id, receipt_date=body.receipt_date,
            warehouse_id=body.warehouse_id, location_id=body.location_id, notes=body.notes,
            lines=[line.model_dump() for line in body.lines],
        )
        return _grn_out(db, g)


def _grn_out(db: DBSession, g: GoodsReceipt) -> dict[str, Any]:
    lines = purchasing.goods_receipt_lines(db, g.id)
    return {
        "id": g.id, "number": g.number, "receipt_date": g.receipt_date, "purchase_order_id": g.purchase_order_id,
        "warehouse_id": g.warehouse_id, "location_id": g.location_id, "status": g.status, "notes": g.notes,
        "lines": [
            {"id": x.id, "line_number": x.line_number, "purchase_order_line_id": x.purchase_order_line_id,
             "product_id": x.product_id, "quantity": _q(x.quantity), "unit_cost": _c(x.unit_cost)}
            for x in lines
        ],
    }


@router.get("/goods-receipts/{receipt_id}")
def get_goods_receipt(receipt_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return _grn_out(db, purchasing.get_goods_receipt(db, receipt_id))


# ---------------------------------------------------------------- purchase bills


@router.get("/purchase-bills")
def list_purchase_bills(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": b.id, "number": b.number, "supplier_invoice_number": b.supplier_invoice_number, "bill_date": b.bill_date,
         "status": b.status, "party_id": b.party_id, **_header_totals(b)}
        for b in purchasing.list_purchase_bills(db, status_filter)
    ]


@router.post("/purchase-bills", status_code=status.HTTP_201_CREATED)
def create_purchase_bill(body: sc.PurchaseBillIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        b = purchasing.create_purchase_bill(
            db, request, auth, party_id=body.party_id, supplier_invoice_number=body.supplier_invoice_number,
            bill_date=body.bill_date, due_date=body.due_date, purchase_order_id=body.purchase_order_id,
            notes=body.notes, lines=_lines_payload(body.lines),
        )
        return _bill_out(b, purchasing.purchase_bill_lines(db, b.id))


@router.get("/purchase-bills/{bill_id}")
def get_purchase_bill(bill_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        b = purchasing.get_purchase_bill(db, bill_id)
        return _bill_out(b, purchasing.purchase_bill_lines(db, b.id))


@router.post("/purchase-bills/{bill_id}/post")
def post_purchase_bill(bill_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        b = purchasing.post_purchase_bill(db, request, auth, bill_id)
        return _bill_out(b, purchasing.purchase_bill_lines(db, b.id))


@router.post("/purchase-bills/{bill_id}/void")
def void_purchase_bill(bill_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        b = purchasing.void_purchase_bill(db, request, auth, bill_id)
        return _bill_out(b, purchasing.purchase_bill_lines(db, b.id))


@router.post("/purchase-bills/{bill_id}/payment-reference")
def record_bill_payment(bill_id: UUID, body: sc.PaymentReferenceIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        b = purchasing.record_bill_payment(db, request, auth, bill_id, body.reference, body.paid_on)
    return {"id": b.id, "payment_reference": b.payment_reference, "payment_date": b.payment_date}


@router.delete("/purchase-bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_purchase_bill(bill_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        purchasing.discard_purchase_bill(db, request, auth, bill_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- quotations


@router.get("/quotations")
def list_quotations(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": q.id, "number": q.number, "quotation_date": q.quotation_date, "status": q.status, "party_id": q.party_id, **_header_totals(q)}
        for q in sales.list_quotations(db, status_filter)
    ]


@router.post("/quotations", status_code=status.HTTP_201_CREATED)
def create_quotation(body: sc.QuotationIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        q = sales.create_quotation(
            db, request, auth, party_id=body.party_id, quotation_date=body.quotation_date,
            valid_until=body.valid_until, notes=body.notes, lines=_lines_payload(body.lines),
        )
        return _quote_out(q, sales.quotation_lines(db, q.id))


@router.get("/quotations/{quotation_id}")
def get_quotation(quotation_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        q = sales.get_quotation(db, quotation_id)
        return _quote_out(q, sales.quotation_lines(db, q.id))


@router.post("/quotations/{quotation_id}/status")
def set_quotation_status(
    quotation_id: UUID, body: sc.QuotationStatusIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        q = sales.transition_quotation(db, request, auth, quotation_id, body.status)
    return {"id": q.id, "status": q.status}


@router.post("/quotations/{quotation_id}/convert", status_code=status.HTTP_201_CREATED)
def convert_quotation(quotation_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        order = sales.convert_quotation(db, request, auth, quotation_id)
        return _so_out(order, sales.sales_order_lines(db, order.id))


@router.delete("/quotations/{quotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_quotation(quotation_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        sales.discard_quotation(db, request, auth, quotation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- sales orders


@router.get("/sales-orders")
def list_sales_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": o.id, "number": o.number, "order_date": o.order_date, "status": o.status, "party_id": o.party_id, **_header_totals(o)}
        for o in sales.list_sales_orders(db, status_filter)
    ]


@router.post("/sales-orders", status_code=status.HTTP_201_CREATED)
def create_sales_order(body: sc.SalesOrderIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        o = sales.create_sales_order(
            db, request, auth, party_id=body.party_id, order_date=body.order_date, notes=body.notes, lines=_lines_payload(body.lines)
        )
        return _so_out(o, sales.sales_order_lines(db, o.id))


@router.get("/sales-orders/{order_id}")
def get_sales_order(order_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        o = sales.get_sales_order(db, order_id)
        return _so_out(o, sales.sales_order_lines(db, o.id))


@router.post("/sales-orders/{order_id}/status")
def set_sales_order_status(
    order_id: UUID, body: sc.SalesOrderStatusIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        o = sales.transition_sales_order(db, request, auth, order_id, body.status)
        return _so_out(o, sales.sales_order_lines(db, o.id))


@router.delete("/sales-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_sales_order(order_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        sales.discard_sales_order(db, request, auth, order_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- sales invoices


@router.get("/sales-invoices")
def list_sales_invoices(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        {"id": i.id, "number": i.number, "invoice_date": i.invoice_date, "status": i.status, "party_id": i.party_id, **_header_totals(i)}
        for i in sales.list_sales_invoices(db, status_filter)
    ]


@router.post("/sales-invoices", status_code=status.HTTP_201_CREATED)
def create_sales_invoice(body: sc.SalesInvoiceIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        i = sales.create_sales_invoice(
            db, request, auth, sales_order_id=body.sales_order_id, invoice_date=body.invoice_date, due_date=body.due_date,
            warehouse_id=body.warehouse_id, location_id=body.location_id, notes=body.notes,
            lines=[line.model_dump() for line in body.lines],
        )
        return _invoice_out(i, sales.sales_invoice_lines(db, i.id))


@router.get("/sales-invoices/{invoice_id}")
def get_sales_invoice(invoice_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        i = sales.get_sales_invoice(db, invoice_id)
        return _invoice_out(i, sales.sales_invoice_lines(db, i.id))


@router.post("/sales-invoices/{invoice_id}/post")
def post_sales_invoice(invoice_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        i = sales.post_sales_invoice(db, request, auth, invoice_id)
        return _invoice_out(i, sales.sales_invoice_lines(db, i.id))


@router.post("/sales-invoices/{invoice_id}/void")
def void_sales_invoice(invoice_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        i = sales.void_sales_invoice(db, request, auth, invoice_id)
        return _invoice_out(i, sales.sales_invoice_lines(db, i.id))


@router.post("/sales-invoices/{invoice_id}/payment-reference")
def record_invoice_payment(invoice_id: UUID, body: sc.PaymentReferenceIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        i = sales.record_invoice_payment(db, request, auth, invoice_id, body.reference, body.paid_on)
    return {"id": i.id, "payment_reference": i.payment_reference, "payment_received_on": i.payment_received_on}


@router.delete("/sales-invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_sales_invoice(invoice_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> Response:
    with _translate():
        sales.discard_sales_invoice(db, request, auth, invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- document processing


@router.get("/document-jobs")
def list_document_jobs(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [_job_out(job) for job in documents.list_jobs(db, status_filter)]


@router.post("/document-jobs", status_code=status.HTTP_201_CREATED)
def create_document_job(
    body: sc.ProcessingJobIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db), provider: DocumentExtractionProvider = Depends(get_extraction_provider),
) -> dict[str, Any]:
    with _translate():
        document = get_owner_document(db, auth.owner.id, body.document_id)
        job = documents.create_job(db, request, auth, document=document, provider_name=provider.name)
    return _job_out(job, detail=True)


@router.get("/document-jobs/{job_id}")
def get_document_job(job_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return _job_out(documents.get_job(db, job_id), detail=True)


@router.post("/document-jobs/{job_id}/run")
def run_document_job(
    job_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db), provider: DocumentExtractionProvider = Depends(get_extraction_provider),
    storage: LocalFileStorage = Depends(get_storage), cipher: SensitiveDataCipher = Depends(get_cipher),
) -> dict[str, Any]:
    with _translate():
        job = documents.get_job(db, job_id)
        document = get_owner_document(db, auth.owner.id, job.document_id)

        def read_content() -> tuple[bytes, str]:
            file = db.scalar(
                select(DocumentFile)
                .where(
                    DocumentFile.document_id == document.id,
                    DocumentFile.scan_status == FileScanStatus.CLEAN.value,
                    DocumentFile.storage_deleted_at.is_(None),
                )
                .order_by(DocumentFile.created_at.desc())
                .limit(1)
            )
            if file is None:
                raise APIError(409, "document_not_available", "No scanned file is available for this document.")
            content = read_for_download(
                db=db, request=request, owner_id=auth.owner.id, session_id=auth.session.id,
                document=document, file=file, storage=storage, cipher=cipher,
            )
            return content, file.content_type

        job = documents.run_job(db, request, auth, job_id, provider=provider, read_content=read_content)
    return _job_out(job, detail=True)


@router.patch("/document-jobs/{job_id}")
def correct_document_job(
    job_id: UUID, body: sc.ProcessingCorrectionIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    with _translate():
        return _job_out(documents.correct_job(db, request, auth, job_id, changes), detail=True)


@router.post("/document-jobs/{job_id}/approve")
def approve_document_job(
    job_id: UUID, body: sc.ProcessingApproveIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        job, target, created_id = documents.approve_job(db, request, auth, job_id, acknowledge_warnings=body.acknowledge_warnings)
    return {"job": _job_out(job), "draft_type": target, "draft_id": created_id}


@router.post("/document-jobs/{job_id}/reject")
def reject_document_job(
    job_id: UUID, body: sc.ProcessingRejectIn, request: Request,
    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        return _job_out(documents.reject_job(db, request, auth, job_id, body.reason))

