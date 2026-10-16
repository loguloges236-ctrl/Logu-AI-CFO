"""Purchasing: Purchase Request -> Purchase Order -> Goods Receipt (partial allowed) -> Purchase Bill -> payment reference.

Business rules:
- a goods receipt cannot exceed the ordered quantity unless COMMERCE_ALLOW_OVER_RECEIPT is set;
- a bill's stock lines must come from a goods receipt, and cannot exceed the received quantity not yet billed;
- a stock line's price is the receipt's unit cost, so the bill and the stock valuation always agree;
- posting a bill posts one balanced journal entry through the Phase 2 accounting service, in the same transaction;
- a posted bill is voided by reversal, never edited or deleted. Payment is recorded as a reference only.
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
from app.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseBill,
    PurchaseBillLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequest,
    PurchaseRequestLine,
)
from app.services import commerce_catalog as catalog
from app.services import commerce_ledger as ledger
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import (
    audit,
    discard_draft,
    flush,
    next_number,
    not_found,
    rule,
    unit_of_work,
)
from app.services.commerce_inventory import move_stock
from app.services.commerce_pricing import apply_totals, price_lines


def _lock(db: DBSession, model: Any, row_id: UUID) -> Any:
    row = db.scalar(select(model).where(model.id == row_id).with_for_update())
    if row is None:
        raise not_found(model.__name__.replace("_", " "))
    return row


# ---------------------------------------------------------------- purchase requests


def list_purchase_requests(db: DBSession, status: str | None = None) -> list[PurchaseRequest]:
    stmt = select(PurchaseRequest).order_by(PurchaseRequest.request_date.desc(), PurchaseRequest.number.desc())
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_purchase_request(db: DBSession, request_id: UUID) -> PurchaseRequest:
    row = db.get(PurchaseRequest, request_id)
    if row is None:
        raise not_found("Purchase request")
    return row


def create_purchase_request(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    request_date: date,
    requested_by: str | None,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> PurchaseRequest:
    cd.validate_line_count(len(lines))
    prepared: list[tuple[UUID | None, str, Decimal]] = []
    for raw in lines:
        product_id = raw.get("product_id")
        if product_id is not None:
            product = catalog.require_active_product(db, product_id, stock=False)
            description = (raw.get("description") or "").strip() or product.name
        else:
            description = cd.validate_name(raw.get("description") or "", "Line description")
            product = None
        prepared.append((product.id if product else None, description, cd.parse_quantity(raw.get("quantity"))))
    with unit_of_work(db):
        header = PurchaseRequest(
            id=uuid.uuid4(),
            number=next_number(db, "purchase_request"),
            request_date=request_date,
            status="draft",
            requested_by=(requested_by or None),
            notes=(notes or None),
            created_by_owner_id=auth.owner.id,
        )
        db.add(header)
        flush(db)
        for number, (product_id, description, quantity) in enumerate(prepared, start=1):
            db.add(
                PurchaseRequestLine(
                    id=uuid.uuid4(),
                    purchase_request_id=header.id,
                    line_number=number,
                    product_id=product_id,
                    description=description,
                    quantity=quantity,
                )
            )
        audit(db, request, auth, "purchase_request.created", "purchase_request", header.number, {"lines": len(prepared)})
    return header


def decide_purchase_request(
    db: DBSession, request: Request, auth: AuthenticatedSession, request_id: UUID, decision: str
) -> PurchaseRequest:
    if decision not in ("approve", "reject"):
        raise rule("invalid_decision", "Decision must be approve or reject")
    target = "approved" if decision == "approve" else "rejected"
    with unit_of_work(db):
        header = _lock(db, PurchaseRequest, request_id)
        cd.require_transition("purchase_request", header.status, target)
        header.status = target
        audit(db, request, auth, f"purchase_request.{target}", "purchase_request", header.number)
    return header


def discard_purchase_request(db: DBSession, request: Request, auth: AuthenticatedSession, request_id: UUID) -> None:
    header = get_purchase_request(db, request_id)
    discard_draft(db, request, auth, header=header, line_model=PurchaseRequestLine, line_column=PurchaseRequestLine.purchase_request_id, kind="purchase_request")


# ---------------------------------------------------------------- purchase orders


def list_purchase_orders(db: DBSession, status: str | None = None) -> list[PurchaseOrder]:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.number.desc())
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_purchase_order(db: DBSession, order_id: UUID) -> PurchaseOrder:
    row = db.get(PurchaseOrder, order_id)
    if row is None:
        raise not_found("Purchase order")
    return row


def purchase_order_lines(db: DBSession, order_id: UUID) -> list[PurchaseOrderLine]:
    return list(
        db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == order_id).order_by(PurchaseOrderLine.line_number))
    )


def receipt_summary(db: DBSession, order_id: UUID) -> list[dict[str, Any]]:
    """Ordered, received and billed quantities per line, so partial receipts and bills are visible."""
    rows = []
    for line in purchase_order_lines(db, order_id):
        received = db.scalar(select(func.coalesce(func.sum(GoodsReceiptLine.quantity), 0)).where(GoodsReceiptLine.purchase_order_line_id == line.id))
        billed = db.scalar(
            select(func.coalesce(func.sum(PurchaseBillLine.quantity), 0))
            .join(GoodsReceiptLine, GoodsReceiptLine.id == PurchaseBillLine.goods_receipt_line_id)
            .join(PurchaseBill, PurchaseBill.id == PurchaseBillLine.purchase_bill_id)
            .where(GoodsReceiptLine.purchase_order_line_id == line.id, PurchaseBill.status != "voided")
        )
        rows.append(
            {
                "purchase_order_line_id": line.id,
                "line_number": line.line_number,
                "description": line.description,
                "ordered": line.quantity,
                "received": Decimal(received or 0),
                "billed": Decimal(billed or 0),
            }
        )
    return rows


def build_purchase_order(
    db: DBSession,
    *,
    party_id: UUID,
    order_date: date,
    expected_date: date | None,
    purchase_request_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
    owner_id: UUID | None,
) -> PurchaseOrder:
    """Assemble a draft purchase order without committing. Used directly and by the document-approval flow."""
    catalog.require_party(db, party_id, "supplier")
    if expected_date is not None and expected_date < order_date:
        raise rule("invalid_dates", "The expected date cannot be before the order date")
    linked: PurchaseRequest | None = None
    if purchase_request_id is not None:
        linked = get_purchase_request(db, purchase_request_id)
        if linked.status != "approved":
            raise rule("purchase_request_not_approved", "Only an approved purchase request can be ordered")
    priced = price_lines(db, lines, rate_default="product_purchase")
    header = PurchaseOrder(
        id=uuid.uuid4(),
        number=next_number(db, "purchase_order"),
        order_date=order_date,
        expected_date=expected_date,
        party_id=party_id,
        purchase_request_id=purchase_request_id,
        status="draft",
        notes=(notes or None),
        created_by_owner_id=owner_id,
    )
    apply_totals(header, priced)
    db.add(header)
    flush(db)
    for number, line in enumerate(priced, start=1):
        db.add(
            PurchaseOrderLine(
                id=uuid.uuid4(),
                purchase_order_id=header.id,
                line_number=number,
                product_id=line.product_id,
                description=line.description,
                quantity=line.quantity,
                rate=line.rate,
                discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
                line_subtotal=line.amounts.subtotal,
                line_discount=line.amounts.discount,
                taxable_amount=line.amounts.taxable,
                tax_amount=line.amounts.tax,
                line_total=line.amounts.total,
            )
        )
    if linked is not None:
        cd.require_transition("purchase_request", linked.status, "ordered")
        linked.status = "ordered"
    return header


def create_purchase_order(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    party_id: UUID,
    order_date: date,
    expected_date: date | None,
    purchase_request_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> PurchaseOrder:
    with unit_of_work(db):
        header = build_purchase_order(
            db,
            party_id=party_id,
            order_date=order_date,
            expected_date=expected_date,
            purchase_request_id=purchase_request_id,
            notes=notes,
            lines=lines,
            owner_id=auth.owner.id,
        )
        audit(db, request, auth, "purchase_order.created", "purchase_order", header.number, {"lines": len(lines)})
    return header


def transition_purchase_order(
    db: DBSession, request: Request, auth: AuthenticatedSession, order_id: UUID, target: str
) -> PurchaseOrder:
    if target not in ("approved", "cancelled"):
        raise rule("invalid_transition", "A purchase order can only be approved or cancelled here")
    with unit_of_work(db):
        header = _lock(db, PurchaseOrder, order_id)
        cd.require_transition("purchase_order", header.status, target)
        if target == "cancelled" and db.scalar(select(GoodsReceipt.id).where(GoodsReceipt.purchase_order_id == header.id).limit(1)):
            raise rule("po_has_receipts", "A purchase order with goods receipts cannot be cancelled")
        header.status = target
        audit(db, request, auth, f"purchase_order.{target}", "purchase_order", header.number)
    return header


def discard_purchase_order(db: DBSession, request: Request, auth: AuthenticatedSession, order_id: UUID) -> None:
    header = get_purchase_order(db, order_id)
    discard_draft(db, request, auth, header=header, line_model=PurchaseOrderLine, line_column=PurchaseOrderLine.purchase_order_id, kind="purchase_order")


# ---------------------------------------------------------------- goods receipts


def list_goods_receipts(db: DBSession, order_id: UUID | None = None) -> list[GoodsReceipt]:
    stmt = select(GoodsReceipt).order_by(GoodsReceipt.receipt_date.desc(), GoodsReceipt.number.desc())
    if order_id is not None:
        stmt = stmt.where(GoodsReceipt.purchase_order_id == order_id)
    return list(db.scalars(stmt.limit(200)))


def get_goods_receipt(db: DBSession, receipt_id: UUID) -> GoodsReceipt:
    row = db.get(GoodsReceipt, receipt_id)
    if row is None:
        raise not_found("Goods receipt")
    return row


def goods_receipt_lines(db: DBSession, receipt_id: UUID) -> list[GoodsReceiptLine]:
    return list(db.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.goods_receipt_id == receipt_id).order_by(GoodsReceiptLine.line_number)))


def create_goods_receipt(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    purchase_order_id: UUID,
    receipt_date: date,
    warehouse_id: UUID,
    location_id: UUID,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> GoodsReceipt:
    cd.validate_line_count(len(lines))
    settings = get_settings()
    _, warehouse = catalog.require_location(db, location_id, warehouse_id)
    parsed = [
        (UUID(str(raw["purchase_order_line_id"])), cd.parse_quantity(raw.get("quantity")))
        for raw in lines
    ]
    with unit_of_work(db):
        order = _lock(db, PurchaseOrder, purchase_order_id)  # serialises receipts against this order
        if order.status not in ("approved", "partially_received"):
            raise rule("po_not_receivable", "Goods can only be received against an approved purchase order")
        grn = GoodsReceipt(
            id=uuid.uuid4(),
            number=next_number(db, "goods_receipt"),
            receipt_date=receipt_date,
            purchase_order_id=order.id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            status="posted",
            notes=(notes or None),
            created_by_owner_id=auth.owner.id,
        )
        db.add(grn)
        flush(db)
        for number, (order_line_id, quantity) in enumerate(parsed, start=1):
            order_line = _lock(db, PurchaseOrderLine, order_line_id)
            if order_line.purchase_order_id != order.id:
                raise rule("invalid_line", "A line does not belong to this purchase order")
            if order_line.product_id is None:
                raise rule("not_stock_item", "A service line on a purchase order cannot be received into stock")
            product = catalog.require_active_product(db, order_line.product_id)
            received_before = db.scalar(
                select(func.coalesce(func.sum(GoodsReceiptLine.quantity), 0)).where(GoodsReceiptLine.purchase_order_line_id == order_line.id)
            )
            cd.check_receipt_quantity(
                ordered=order_line.quantity,
                received_before=Decimal(received_before or 0),
                new_quantity=quantity,
                allow_over=settings.commerce_allow_over_receipt,
            )
            unit_cost = cd.q4(order_line.rate * (Decimal("100") - order_line.discount_percent) / Decimal("100"))
            grn_line = GoodsReceiptLine(
                id=uuid.uuid4(),
                goods_receipt_id=grn.id,
                line_number=number,
                purchase_order_line_id=order_line.id,
                product_id=product.id,
                quantity=quantity,
                unit_cost=unit_cost,
            )
            db.add(grn_line)
            flush(db)
            move_stock(
                db,
                owner_id=auth.owner.id,
                product_id=product.id,
                location_id=location_id,
                allow_negative=warehouse.allow_negative_stock,
                movement_type=cd.MovementType.GRN_IN,
                quantity_delta=quantity,
                unit_cost=unit_cost,
                source_type="goods_receipt",
                source_id=grn.id,
                source_line_id=grn_line.id,
            )
        ordered_total = db.scalar(select(func.coalesce(func.sum(PurchaseOrderLine.quantity), 0)).where(PurchaseOrderLine.purchase_order_id == order.id))
        received_total = db.scalar(
            select(func.coalesce(func.sum(GoodsReceiptLine.quantity), 0))
            .join(PurchaseOrderLine, PurchaseOrderLine.id == GoodsReceiptLine.purchase_order_line_id)
            .where(PurchaseOrderLine.purchase_order_id == order.id)
        )
        target = cd.po_status_after_receipt(Decimal(ordered_total or 0), Decimal(received_total or 0))
        if target != order.status:
            cd.require_transition("purchase_order", order.status, target)
            order.status = target
        audit(db, request, auth, "goods_receipt.created", "goods_receipt", grn.number, {"lines": len(parsed), "order_status": target})
        audit(db, request, auth, "stock.grn_in", "goods_receipt", grn.number, {"lines": len(parsed)})
    return grn


# ---------------------------------------------------------------- purchase bills


def list_purchase_bills(db: DBSession, status: str | None = None) -> list[PurchaseBill]:
    stmt = select(PurchaseBill).order_by(PurchaseBill.bill_date.desc(), PurchaseBill.number.desc())
    if status:
        stmt = stmt.where(PurchaseBill.status == status)
    return list(db.scalars(stmt.limit(200)))


def get_purchase_bill(db: DBSession, bill_id: UUID) -> PurchaseBill:
    row = db.get(PurchaseBill, bill_id)
    if row is None:
        raise not_found("Purchase bill")
    return row


def purchase_bill_lines(db: DBSession, bill_id: UUID) -> list[PurchaseBillLine]:
    return list(db.scalars(select(PurchaseBillLine).where(PurchaseBillLine.purchase_bill_id == bill_id).order_by(PurchaseBillLine.line_number)))


def _billed_quantity(db: DBSession, grn_line_id: UUID, *, posted_only: bool, exclude_bill: UUID | None = None) -> Decimal:
    stmt = (
        select(func.coalesce(func.sum(PurchaseBillLine.quantity), 0))
        .join(PurchaseBill, PurchaseBill.id == PurchaseBillLine.purchase_bill_id)
        .where(PurchaseBillLine.goods_receipt_line_id == grn_line_id)
    )
    if posted_only:
        stmt = stmt.where(PurchaseBill.status == "posted")
    else:
        stmt = stmt.where(PurchaseBill.status != "voided")
    if exclude_bill is not None:
        stmt = stmt.where(PurchaseBill.id != exclude_bill)
    return Decimal(db.scalar(stmt) or 0)


def build_purchase_bill(
    db: DBSession,
    *,
    party_id: UUID,
    supplier_invoice_number: str,
    bill_date: date,
    due_date: date | None,
    purchase_order_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
    owner_id: UUID | None,
) -> PurchaseBill:
    """Assemble a draft bill without committing. Used directly and by the document-approval flow."""
    catalog.require_party(db, party_id, "supplier")
    invoice_no = supplier_invoice_number.strip()
    if not invoice_no or len(invoice_no) > 60:
        raise rule("invalid_supplier_invoice", "Supplier invoice number is required (at most 60 characters)")
    if due_date is not None and due_date < bill_date:
        raise rule("invalid_dates", "The due date cannot be before the bill date")
    if db.scalar(
        select(PurchaseBill.id).where(PurchaseBill.party_id == party_id, func.lower(PurchaseBill.supplier_invoice_number) == invoice_no.lower()).limit(1)
    ) is not None:
        raise rule("duplicate_supplier_invoice", "This supplier invoice number is already recorded for this supplier")
    cd.validate_line_count(len(lines))

    priced: list[tuple[dict[str, Any], Any, UUID | None]] = []  # (raw, priced_line, expense_account)
    order_ids: set[UUID] = set()
    for raw in lines:
        if raw.get("goods_receipt_line_id") is not None:
            grn_line = db.get(GoodsReceiptLine, UUID(str(raw["goods_receipt_line_id"])))
            if grn_line is None:
                raise not_found("Goods receipt line")
            product = catalog.require_active_product(db, grn_line.product_id)
            quantity = cd.parse_quantity(raw.get("quantity"))
            receipt = get_goods_receipt(db, grn_line.goods_receipt_id)
            order_ids.add(receipt.purchase_order_id)
            cd.check_bill_quantity(
                received=grn_line.quantity,
                billed_before=_billed_quantity(db, grn_line.id, posted_only=False),
                new_quantity=quantity,
            )
            tax = cd.parse_percent(raw.get("tax_rate"), "tax_rate") if raw.get("tax_rate") not in (None, "") else (
                Decimal(str(product.tax_rate)) if product.tax_rate is not None else Decimal("0")
            )
            # The price comes from the receipt, so bill and stock valuation agree.
            amounts = cd.line_amounts(quantity, grn_line.unit_cost, Decimal("0"), tax)
            line = _PricedBillLine(product.id, product.name, quantity, grn_line.unit_cost, Decimal("0"), tax, amounts, grn_line.id)
            priced.append((raw, line, None))
        else:
            if raw.get("product_id") is not None:
                product = catalog.require_active_product(db, raw["product_id"], stock=False)
                if product.is_stock_item:
                    raise rule("requires_goods_receipt", f"{product.sku} is a stock item. Bill it against a goods receipt.")
            account_id = raw.get("expense_account_id")
            if account_id is None:
                raise rule("expense_account_required", "Choose an expense account for each non-stock line")
            ledger.account_is_usable(db, account_id, "expense")
            description = cd.validate_name(raw.get("description") or "", "Line description")
            quantity = cd.parse_quantity(raw.get("quantity"))
            rate = cd.parse_rate(raw.get("rate"), "rate")
            discount = cd.parse_percent(raw.get("discount_percent"), "discount_percent")
            tax = cd.parse_percent(raw.get("tax_rate"), "tax_rate")
            amounts = cd.line_amounts(quantity, rate, discount, tax)
            line = _PricedBillLine(raw.get("product_id"), description, quantity, rate, discount, tax, amounts, None)
            priced.append((raw, line, account_id))

    if len(order_ids) > 1:
        raise rule("mixed_purchase_orders", "Goods from more than one purchase order cannot be billed together")
    linked_order = purchase_order_id or (next(iter(order_ids)) if order_ids else None)
    totals = cd.document_totals(item[1].amounts for item in priced)
    bill = PurchaseBill(
        id=uuid.uuid4(),
        number=next_number(db, "purchase_bill"),
        supplier_invoice_number=invoice_no,
        bill_date=bill_date,
        due_date=due_date,
        party_id=party_id,
        purchase_order_id=linked_order,
        status="draft",
        subtotal=totals.subtotal,
        discount_total=totals.discount,
        taxable_total=totals.taxable,
        tax_total=totals.tax,
        grand_total=totals.grand_total,
        notes=(notes or None),
        created_by_owner_id=owner_id,
    )
    db.add(bill)
    flush(db)
    for number, (_, line, account_id) in enumerate(priced, start=1):
        db.add(
            PurchaseBillLine(
                id=uuid.uuid4(),
                purchase_bill_id=bill.id,
                line_number=number,
                goods_receipt_line_id=line.grn_line_id,
                product_id=line.product_id,
                description=line.description,
                expense_account_id=account_id,
                quantity=line.quantity,
                rate=line.rate,
                discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
                line_subtotal=line.amounts.subtotal,
                line_discount=line.amounts.discount,
                taxable_amount=line.amounts.taxable,
                tax_amount=line.amounts.tax,
                line_total=line.amounts.total,
            )
        )
    return bill


class _PricedBillLine:
    __slots__ = ("product_id", "description", "quantity", "rate", "discount_percent", "tax_rate", "amounts", "grn_line_id")

    def __init__(self, product_id, description, quantity, rate, discount_percent, tax_rate, amounts, grn_line_id):  # noqa: ANN001
        self.product_id = product_id
        self.description = description
        self.quantity = quantity
        self.rate = rate
        self.discount_percent = discount_percent
        self.tax_rate = tax_rate
        self.amounts = amounts
        self.grn_line_id = grn_line_id


def create_purchase_bill(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    party_id: UUID,
    supplier_invoice_number: str,
    bill_date: date,
    due_date: date | None,
    purchase_order_id: UUID | None,
    notes: str | None,
    lines: list[dict[str, Any]],
) -> PurchaseBill:
    with unit_of_work(db):
        bill = build_purchase_bill(
            db,
            party_id=party_id,
            supplier_invoice_number=supplier_invoice_number,
            bill_date=bill_date,
            due_date=due_date,
            purchase_order_id=purchase_order_id,
            notes=notes,
            lines=lines,
            owner_id=auth.owner.id,
        )
        audit(db, request, auth, "purchase_bill.created", "purchase_bill", bill.number, {"lines": len(lines)})
    return bill


def post_purchase_bill(db: DBSession, request: Request, auth: AuthenticatedSession, bill_id: UUID) -> PurchaseBill:
    """Post the bill: re-check quantities under lock, then post one balanced journal entry in the same transaction."""
    with unit_of_work(db):
        bill = _lock(db, PurchaseBill, bill_id)
        cd.require_transition("purchase_bill", bill.status, "posted")
        supplier = catalog.require_party(db, bill.party_id, "supplier")
        roles = ledger.require_roles(db, cd.REQUIRED_ROLES_FOR_PURCHASE_BILL)
        stock_taxable = Decimal("0")
        expense_postings: list[tuple[UUID, Decimal, bool]] = []
        for line in purchase_bill_lines(db, bill.id):
            if line.goods_receipt_line_id is not None:
                grn_line = _lock(db, GoodsReceiptLine, line.goods_receipt_line_id)
                cd.check_bill_quantity(
                    received=grn_line.quantity,
                    billed_before=_billed_quantity(db, grn_line.id, posted_only=True, exclude_bill=bill.id),
                    new_quantity=line.quantity,
                )
                if cd.q2(line.quantity * grn_line.unit_cost) != line.taxable_amount:
                    raise rule("bill_price_mismatch", "The stock line price no longer matches the goods receipt")
                stock_taxable += line.taxable_amount
            else:
                expense_postings.append((line.expense_account_id, line.taxable_amount, True))
        postings: list[tuple[UUID, Decimal, bool]] = list(expense_postings)
        if stock_taxable > 0:
            postings.append((roles["inventory"], stock_taxable, True))
        if bill.tax_total > 0:
            postings.append((roles["input_tax"], bill.tax_total, True))
        postings.append((roles["accounts_payable"], bill.grand_total, False))
        entry = ledger.post_entry(
            db,
            request,
            auth,
            description=f"Purchase bill {bill.number} ({bill.supplier_invoice_number}) from {supplier.name}",
            posting_date=bill.bill_date,
            reference_type="purchase_bill",
            reference_id=str(bill.id),
            postings=postings,
            action="purchase_bill.posted",
        )
        bill.status = "posted"
        bill.journal_entry_id = entry.id
        audit(db, request, auth, "purchase_bill.posted", "purchase_bill", bill.number, {"journal_entry_number": entry.entry_number})
    return bill


def void_purchase_bill(db: DBSession, request: Request, auth: AuthenticatedSession, bill_id: UUID) -> PurchaseBill:
    with unit_of_work(db):
        bill = _lock(db, PurchaseBill, bill_id)
        cd.require_transition("purchase_bill", bill.status, "voided")
        reversal = ledger.reverse_entry(
            db, request, auth, bill.journal_entry_id, posting_date=utcnow().date(),
            reference_type="purchase_bill", reference_id=str(bill.id),
        )
        bill.status = "voided"
        bill.reversal_entry_id = reversal.id
        bill.voided_at = utcnow()
        audit(db, request, auth, "purchase_bill.voided", "purchase_bill", bill.number)
    return bill


def record_bill_payment(
    db: DBSession, request: Request, auth: AuthenticatedSession, bill_id: UUID, reference: str, paid_on: date
) -> PurchaseBill:
    """Record a payment reference only. No money moves and no journal entry is written here."""
    value = reference.strip()
    if not value or len(value) > 120:
        raise rule("invalid_reference", "Payment reference is required (at most 120 characters)")
    with unit_of_work(db):
        bill = _lock(db, PurchaseBill, bill_id)
        if bill.status != "posted":
            raise rule("not_posted", "Payment references can only be recorded on a posted bill")
        bill.payment_reference = value
        bill.payment_date = paid_on
        audit(db, request, auth, "purchase_bill.payment_reference_recorded", "purchase_bill", bill.number)
    return bill


def discard_purchase_bill(db: DBSession, request: Request, auth: AuthenticatedSession, bill_id: UUID) -> None:
    bill = get_purchase_bill(db, bill_id)
    discard_draft(db, request, auth, header=bill, line_model=PurchaseBillLine, line_column=PurchaseBillLine.purchase_bill_id, kind="purchase_bill")
