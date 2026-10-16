"""Sales: Quotation -> Sales Order -> Invoice -> payment reference.

Business rules:
- an invoice can only be raised against a confirmed sales order, and cannot exceed the ordered quantity unless
  COMMERCE_ALLOW_OVER_INVOICE is set; partial invoicing follows COMMERCE_ALLOW_PARTIAL_INVOICE;
- posting an invoice dispatches stock (stock items) and posts one balanced journal entry in the same transaction:
  Dr receivable / Cr sales / Cr output tax, and Dr cost of goods / Cr inventory at the weighted-average cost;
- a posted invoice is voided by reversal and the goods return to stock at their recorded cost;
- payment is recorded as a reference only.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.commerce import domain as cd
from app.core.clock import utcnow
from app.core.config import get_settings
from app.models import Quotation, QuotationLine, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine
from app.services import commerce_catalog as catalog
from app.services import commerce_ledger as ledger
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, discard_draft, flush, next_number, not_found, rule, unit_of_work
from app.services.commerce_inventory import move_stock
from app.services.commerce_pricing import apply_totals, price_lines

def _lock(db: DBSession, model: Any, row_id: UUID) -> Any:
    row = db.scalar(select(model).where(model.id == row_id).with_for_update())
    if row is None:
        raise not_found(model.__name__.replace("_", " "))
    return row


def _priced_line_fields(line: Any) -> dict[str, Any]:
    return {
        "product_id": line.product_id,
        "description": line.description,
        "quantity": line.quantity,
        "rate": line.rate,
        "discount_percent": line.discount_percent,
        "tax_rate": line.tax_rate,
        "line_subtotal": line.amounts.subtotal,
        "line_discount": line.amounts.discount,
        "taxable_amount": line.amounts.taxable,
        "tax_amount": line.amounts.tax,
        "line_total": line.amounts.total,
    }


# ---------------------------------------------------------------- quotations


def list_quotations(db: DBSession, status: str | None = None) -> list[Quotation]:
    stmt = select(Quotation).order_by(Quotation.quotation_date.desc(), Quotation.number.desc())
    if status:
        stmt = stmt.where(Quotation.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_quotation(db: DBSession, quotation_id: UUID) -> Quotation:
    row = db.get(Quotation, quotation_id)
    if row is None:
        raise not_found("Quotation")
    return row


def quotation_lines(db: DBSession, quotation_id: UUID) -> list[QuotationLine]:
    return list(db.scalars(select(QuotationLine).where(QuotationLine.quotation_id == quotation_id).order_by(QuotationLine.line_number)))


def build_quotation(
    db: DBSession,
    *,
    party_id: UUID,
    quotation_date: date,
    valid_until: date | None,
    notes: str | None,
    lines: list[dict[str, Any]],
    owner_id: UUID | None,
) -> Quotation:
    catalog.require_party(db, party_id, "customer")
    if valid_until is not None and valid_until < quotation_date:
        raise rule("invalid_dates", "The validity date cannot be before the quotation date")
    priced = price_lines(db, lines, rate_default="product_selling")
    header = Quotation(
        id=uuid.uuid4(),
        number=next_number(db, "quotation"),
        quotation_date=quotation_date,
        valid_until=valid_until,
        party_id=party_id,
        status="draft",
        notes=(notes or None),
        created_by_owner_id=owner_id,
    )
    apply_totals(header, priced)
    db.add(header)
    flush(db)
    for number, line in enumerate(priced, start=1):
        db.add(QuotationLine(id=uuid.uuid4(), quotation_id=header.id, line_number=number, **_priced_line_fields(line)))
    return header


def create_quotation(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    party_id: UUID,
    quotation_date: date,
    valid_until: date | None,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> Quotation:
    with unit_of_work(db):
        header = build_quotation(
            db,
            party_id=party_id,
            quotation_date=quotation_date,
            valid_until=valid_until,
            notes=notes,
            lines=lines,
            owner_id=auth.owner.id,
        )
        audit(db, request, auth, "quotation.created", "quotation", header.number, {"lines": len(lines)})
    return header


def transition_quotation(db: DBSession, request: Request, auth: AuthenticatedSession, quotation_id: UUID, target: str) -> Quotation:
    if target not in ("sent", "accepted", "rejected", "expired"):
        raise rule("invalid_transition", "Unknown quotation status")
    with unit_of_work(db):
        header = _lock(db, Quotation, quotation_id)
        cd.require_transition("quotation", header.status, target)
        header.status = target
        audit(db, request, auth, f"quotation.{target}", "quotation", header.number)
    return header


def discard_quotation(db: DBSession, request: Request, auth: AuthenticatedSession, quotation_id: UUID) -> None:
    header = get_quotation(db, quotation_id)
    discard_draft(db, request, auth, header=header, line_model=QuotationLine, line_column=QuotationLine.quotation_id, kind="quotation")


def convert_quotation(db: DBSession, request: Request, auth: AuthenticatedSession, quotation_id: UUID) -> SalesOrder:
    """Accepted quotation -> draft sales order with the same lines and prices."""
    with unit_of_work(db):
        quotation = _lock(db, Quotation, quotation_id)
        cd.require_transition("quotation", quotation.status, "converted")
        order = _build_sales_order(
            db,
            party_id=quotation.party_id,
            order_date=utcnow().date(),
            notes=quotation.notes,
            quotation_id=quotation.id,
            lines=[
                {
                    "product_id": line.product_id,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "rate": str(line.rate),
                    "discount_percent": str(line.discount_percent),
                    "tax_rate": format(line.tax_rate.normalize(), "f"),
                }
                for line in quotation_lines(db, quotation.id)
            ],
            owner_id=auth.owner.id,
        )
        quotation.status = "converted"
        audit(db, request, auth, "quotation.converted", "quotation", quotation.number, {"sales_order": order.number})
    return order


# ---------------------------------------------------------------- sales orders


def list_sales_orders(db: DBSession, status: str | None = None) -> list[SalesOrder]:
    stmt = select(SalesOrder).order_by(SalesOrder.order_date.desc(), SalesOrder.number.desc())
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_sales_order(db: DBSession, order_id: UUID) -> SalesOrder:
    row = db.get(SalesOrder, order_id)
    if row is None:
        raise not_found("Sales order")
    return row


def sales_order_lines(db: DBSession, order_id: UUID) -> list[SalesOrderLine]:
    return list(db.scalars(select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order_id).order_by(SalesOrderLine.line_number)))


def _build_sales_order(
    db: DBSession,
    *,
    party_id: UUID,
    order_date: date,
    notes: str | None,
    quotation_id: UUID | None,
    lines: list[dict[str, Any]],
    owner_id: UUID | None,
) -> SalesOrder:
    catalog.require_party(db, party_id, "customer")
    priced = price_lines(db, lines, rate_default="product_selling")
    header = SalesOrder(
        id=uuid.uuid4(),
        number=next_number(db, "sales_order"),
        order_date=order_date,
        party_id=party_id,
        quotation_id=quotation_id,
        status="draft",
        notes=(notes or None),
        created_by_owner_id=owner_id,
    )
    apply_totals(header, priced)
    db.add(header)
    flush(db)
    for number, line in enumerate(priced, start=1):
        db.add(
            SalesOrderLine(
                id=uuid.uuid4(), sales_order_id=header.id, line_number=number, invoiced_quantity=Decimal("0"),
                **_priced_line_fields(line),
            )
        )
    return header


def create_sales_order(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    party_id: UUID,
    order_date: date,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> SalesOrder:
    with unit_of_work(db):
        header = _build_sales_order(
            db, party_id=party_id, order_date=order_date, notes=notes, quotation_id=None, lines=lines, owner_id=auth.owner.id
        )
        audit(db, request, auth, "sales_order.created", "sales_order", header.number, {"lines": len(lines)})
    return header


def transition_sales_order(db: DBSession, request: Request, auth: AuthenticatedSession, order_id: UUID, target: str) -> SalesOrder:
    if target not in ("confirmed", "cancelled"):
        raise rule("invalid_transition", "A sales order can only be confirmed or cancelled here")
    with unit_of_work(db):
        header = _lock(db, SalesOrder, order_id)
        cd.require_transition("sales_order", header.status, target)
        if target == "confirmed" and not sales_order_lines(db, header.id):
            raise rule("no_lines", "A sales order needs at least one line")
        if target == "cancelled" and db.scalar(
            select(SalesOrderLine.id).where(SalesOrderLine.sales_order_id == header.id, SalesOrderLine.invoiced_quantity > 0).limit(1)
        ) is not None:
            raise rule("order_has_invoices", "A sales order that has been invoiced cannot be cancelled")
        header.status = target
        audit(db, request, auth, f"sales_order.{target}", "sales_order", header.number)
    return header


def discard_sales_order(db: DBSession, request: Request, auth: AuthenticatedSession, order_id: UUID) -> None:
    header = get_sales_order(db, order_id)
    discard_draft(db, request, auth, header=header, line_model=SalesOrderLine, line_column=SalesOrderLine.sales_order_id, kind="sales_order")


def _invoiced_quantity(db: DBSession, order_line_id: UUID, *, exclude_invoice: UUID | None = None, include_drafts: bool) -> Decimal:
    """Committed invoiced quantity, optionally plus other open drafts (used when a draft is created)."""
    stmt = (
        select(func.coalesce(func.sum(SalesInvoiceLine.quantity), 0))
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.sales_invoice_id)
        .where(SalesInvoiceLine.sales_order_line_id == order_line_id)
    )
    if include_drafts:
        stmt = stmt.where(SalesInvoice.status.in_(("draft", "posted")))
    else:
        stmt = stmt.where(SalesInvoice.status == "posted")
    if exclude_invoice is not None:
        stmt = stmt.where(SalesInvoice.id != exclude_invoice)
    return Decimal(db.scalar(stmt) or 0)


# ---------------------------------------------------------------- invoices


def list_sales_invoices(db: DBSession, status: str | None = None) -> list[SalesInvoice]:
    stmt = select(SalesInvoice).order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.number.desc())
    if status:
        stmt = stmt.where(SalesInvoice.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_sales_invoice(db: DBSession, invoice_id: UUID) -> SalesInvoice:
    row = db.get(SalesInvoice, invoice_id)
    if row is None:
        raise not_found("Sales invoice")
    return row


def sales_invoice_lines(db: DBSession, invoice_id: UUID) -> list[SalesInvoiceLine]:
    return list(db.scalars(select(SalesInvoiceLine).where(SalesInvoiceLine.sales_invoice_id == invoice_id).order_by(SalesInvoiceLine.line_number)))


def build_sales_invoice(
    db: DBSession,
    *,
    sales_order_id: UUID,
    invoice_date: date,
    due_date: date | None,
    warehouse_id: UUID | None,
    location_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
    owner_id: UUID | None,
) -> SalesInvoice:
    """Assemble a draft invoice without committing (no stock or ledger effect until posting)."""
    order = get_sales_order(db, sales_order_id)
    if order.status not in ("confirmed", "partially_invoiced"):
        raise rule("order_not_invoiceable", "Confirm the sales order before invoicing it")
    catalog.require_party(db, order.party_id, "customer")
    if due_date is not None and due_date < invoice_date:
        raise rule("invalid_dates", "The due date cannot be before the invoice date")
    cd.validate_line_count(len(lines))
    settings = get_settings()

    stock_needed = False
    prepared: list[tuple[SalesOrderLine, Decimal]] = []
    for raw in lines:
        order_line = db.get(SalesOrderLine, UUID(str(raw["sales_order_line_id"])))
        if order_line is None or order_line.sales_order_id != order.id:
            raise rule("invalid_line", "A line does not belong to this sales order")
        quantity = cd.parse_quantity(raw.get("quantity"))
        if order_line.product_id is not None and catalog.require_active_product(db, order_line.product_id, stock=False).is_stock_item:
            stock_needed = True
        cd.check_invoice_quantity(
            ordered=order_line.quantity,
            invoiced_before=_invoiced_quantity(db, order_line.id, include_drafts=True),
            new_quantity=quantity,
            allow_over=settings.commerce_allow_over_invoice,
            allow_partial=settings.commerce_allow_partial_invoice,
        )
        prepared.append((order_line, quantity))

    if stock_needed:
        if warehouse_id is None or location_id is None:
            raise rule("location_required", "Choose the warehouse and location the stock is dispatched from")
        catalog.require_location(db, location_id, warehouse_id)
    elif (warehouse_id is None) != (location_id is None):
        raise rule("location_required", "Choose both the warehouse and the location, or neither")

    priced_totals = [
        cd.line_amounts(quantity, line.rate, line.discount_percent, line.tax_rate) for line, quantity in prepared
    ]
    totals = cd.document_totals(priced_totals)
    invoice = SalesInvoice(
        id=uuid.uuid4(),
        number=next_number(db, "sales_invoice"),
        invoice_date=invoice_date,
        due_date=due_date,
        party_id=order.party_id,
        sales_order_id=order.id,
        warehouse_id=warehouse_id,
        location_id=location_id,
        status="draft",
        subtotal=totals.subtotal,
        discount_total=totals.discount,
        taxable_total=totals.taxable,
        tax_total=totals.tax,
        grand_total=totals.grand_total,
        cost_of_goods_total=Decimal("0"),
        notes=(notes or None),
        created_by_owner_id=owner_id,
    )
    db.add(invoice)
    flush(db)
    for number, ((line, quantity), amounts) in enumerate(zip(prepared, priced_totals, strict=True), start=1):
        db.add(
            SalesInvoiceLine(
                id=uuid.uuid4(),
                sales_invoice_id=invoice.id,
                line_number=number,
                sales_order_line_id=line.id,
                product_id=line.product_id,
                description=line.description,
                quantity=quantity,
                rate=line.rate,
                discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
                line_subtotal=amounts.subtotal,
                line_discount=amounts.discount,
                taxable_amount=amounts.taxable,
                tax_amount=amounts.tax,
                line_total=amounts.total,
            )
        )
    return invoice


def create_sales_invoice(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    sales_order_id: UUID,
    invoice_date: date,
    due_date: date | None,
    warehouse_id: UUID | None,
    location_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> SalesInvoice:
    with unit_of_work(db):
        invoice = build_sales_invoice(
            db,
            sales_order_id=sales_order_id,
            invoice_date=invoice_date,
            due_date=due_date,
            warehouse_id=warehouse_id,
            location_id=location_id,
            notes=notes,
            lines=lines,
            owner_id=auth.owner.id,
        )
        audit(db, request, auth, "sales_invoice.created", "sales_invoice", invoice.number, {"lines": len(lines)})
    return invoice


def _refresh_order_status(db: DBSession, order: SalesOrder) -> None:
    """Order status follows the invoiced quantities. Cancelled orders keep their status."""
    if order.status == "cancelled":
        return
    lines = sales_order_lines(db, order.id)
    ordered = sum((line.quantity for line in lines), Decimal("0"))
    invoiced = sum((line.invoiced_quantity for line in lines), Decimal("0"))
    order.status = cd.so_status_after_invoicing(ordered, invoiced)


def post_sales_invoice(db: DBSession, request: Request, auth: AuthenticatedSession, invoice_id: UUID) -> SalesInvoice:
    settings = get_settings()
    with unit_of_work(db):
        invoice = _lock(db, SalesInvoice, invoice_id)
        cd.require_transition("sales_invoice", invoice.status, "posted")
        order = _lock(db, SalesOrder, invoice.sales_order_id)
        if order.status not in ("confirmed", "partially_invoiced"):
            raise rule("order_not_invoiceable", "The sales order is no longer open for invoicing")
        customer = catalog.require_party(db, invoice.party_id, "customer")
        roles = ledger.require_roles(db, cd.REQUIRED_ROLES_FOR_SALES_INVOICE)

        cost_total = Decimal("0")
        pairs: list[tuple[SalesInvoiceLine, SalesOrderLine]] = []
        stock_lines: list[SalesInvoiceLine] = []
        for line in sales_invoice_lines(db, invoice.id):
            order_line = _lock(db, SalesOrderLine, line.sales_order_line_id)
            pairs.append((line, order_line))
            cd.check_invoice_quantity(
                ordered=order_line.quantity,
                invoiced_before=_invoiced_quantity(db, order_line.id, exclude_invoice=invoice.id, include_drafts=False),
                new_quantity=line.quantity,
                allow_over=settings.commerce_allow_over_invoice,
                allow_partial=settings.commerce_allow_partial_invoice,
            )
            if line.product_id is not None and catalog.require_active_product(db, line.product_id, stock=False).is_stock_item:
                stock_lines.append(line)

        if stock_lines:
            if invoice.location_id is None or invoice.warehouse_id is None:
                raise rule("location_required", "Stock lines need a dispatch location")
            _, warehouse = catalog.require_location(db, invoice.location_id, invoice.warehouse_id)
            roles_cogs = ledger.require_roles(db, cd.REQUIRED_ROLES_FOR_COGS)
        else:
            warehouse = None
            roles_cogs = {}

        for line in stock_lines:
            movement = move_stock(
                db,
                owner_id=auth.owner.id,
                product_id=line.product_id,
                location_id=invoice.location_id,
                allow_negative=warehouse.allow_negative_stock,
                movement_type=cd.MovementType.SALE_OUT,
                quantity_delta=-line.quantity,
                unit_cost=None,
                source_type="sales_invoice",
                source_id=invoice.id,
                source_line_id=line.id,
            )
            line.unit_cost = movement.unit_cost
            line.cost_amount = cd.q2(line.quantity * movement.unit_cost)
            cost_total += line.cost_amount

        for line, order_line in pairs:
            order_line.invoiced_quantity = order_line.invoiced_quantity + line.quantity
        invoice.cost_of_goods_total = cd.q2(cost_total)

        postings: list[tuple[UUID, Decimal, bool]] = [(roles["accounts_receivable"], invoice.grand_total, True)]
        postings.append((roles["sales_revenue"], invoice.taxable_total, False))
        if invoice.tax_total > 0:
            postings.append((roles["output_tax"], invoice.tax_total, False))
        if cost_total > 0:
            postings.append((roles_cogs["cost_of_goods_sold"], cost_total, True))
            postings.append((roles_cogs["inventory"], cost_total, False))
        entry = ledger.post_entry(
            db,
            request,
            auth,
            description=f"Sales invoice {invoice.number} to {customer.name}",
            posting_date=invoice.invoice_date,
            reference_type="sales_invoice",
            reference_id=str(invoice.id),
            postings=postings,
            action="sales_invoice.posted",
        )
        invoice.status = "posted"
        invoice.journal_entry_id = entry.id
        _refresh_order_status(db, order)
        audit(db, request, auth, "sales_invoice.posted", "sales_invoice", invoice.number, {"journal_entry_number": entry.entry_number})
    return invoice


def void_sales_invoice(db: DBSession, request: Request, auth: AuthenticatedSession, invoice_id: UUID) -> SalesInvoice:
    with unit_of_work(db):
        invoice = _lock(db, SalesInvoice, invoice_id)
        cd.require_transition("sales_invoice", invoice.status, "voided")
        order = _lock(db, SalesOrder, invoice.sales_order_id)
        reversal = ledger.reverse_entry(
            db, request, auth, invoice.journal_entry_id, posting_date=utcnow().date(),
            reference_type="sales_invoice", reference_id=str(invoice.id),
        )
        for line in sales_invoice_lines(db, invoice.id):
            if line.product_id is not None and line.unit_cost is not None:
                _, warehouse = catalog.require_location(db, invoice.location_id, invoice.warehouse_id)
                move_stock(
                    db,
                    owner_id=auth.owner.id,
                    product_id=line.product_id,
                    location_id=invoice.location_id,
                    allow_negative=warehouse.allow_negative_stock,
                    movement_type=cd.MovementType.RETURN_IN,
                    quantity_delta=line.quantity,
                    unit_cost=line.unit_cost,
                    source_type="sales_invoice_void",
                    source_id=invoice.id,
                    source_line_id=line.id,
                )
            order_line = _lock(db, SalesOrderLine, line.sales_order_line_id)
            order_line.invoiced_quantity = order_line.invoiced_quantity - line.quantity
        invoice.status = "voided"
        invoice.reversal_entry_id = reversal.id
        invoice.voided_at = utcnow()
        _refresh_order_status(db, order)
        audit(db, request, auth, "sales_invoice.voided", "sales_invoice", invoice.number)
    return invoice


def record_invoice_payment(
    db: DBSession, request: Request, auth: AuthenticatedSession, invoice_id: UUID, reference: str, received_on: date
) -> SalesInvoice:
    """Record a receipt reference only. No money moves and no journal entry is written here."""
    value = reference.strip()
    if not value or len(value) > 120:
        raise rule("invalid_reference", "Payment reference is required (at most 120 characters)")
    with unit_of_work(db):
        invoice = _lock(db, SalesInvoice, invoice_id)
        if invoice.status != "posted":
            raise rule("not_posted", "Payment references can only be recorded on a posted invoice")
        invoice.payment_reference = value
        invoice.payment_received_on = received_on
        audit(db, request, auth, "sales_invoice.payment_reference_recorded", "sales_invoice", invoice.number)
    return invoice


def discard_sales_invoice(db: DBSession, request: Request, auth: AuthenticatedSession, invoice_id: UUID) -> None:
    invoice = get_sales_invoice(db, invoice_id)
    discard_draft(db, request, auth, header=invoice, line_model=SalesInvoiceLine, line_column=SalesInvoiceLine.sales_invoice_id, kind="sales_invoice")

