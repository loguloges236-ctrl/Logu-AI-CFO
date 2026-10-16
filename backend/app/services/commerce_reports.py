"""Read-only summaries for the business dashboard. No writes, no ledger access."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.commerce import domain as cd
from app.models import (
    DocumentProcessingJob,
    Product,
    PurchaseBill,
    PurchaseOrder,
    SalesInvoice,
    StockBalance,
)
from app.services import commerce_inventory as inventory


def dashboard(db: DBSession) -> dict[str, Any]:
    open_orders = db.scalar(
        select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status.in_(("approved", "partially_received")))
    ) or 0
    bills_to_post = db.scalar(select(func.count()).select_from(PurchaseBill).where(PurchaseBill.status == "draft")) or 0
    invoices_to_post = db.scalar(select(func.count()).select_from(SalesInvoice).where(SalesInvoice.status == "draft")) or 0
    review_queue = db.scalar(
        select(func.count()).select_from(DocumentProcessingJob).where(DocumentProcessingJob.status.in_(("needs_review", "queued", "failed")))
    ) or 0
    no_stock = db.scalar(
        select(func.count())
        .select_from(StockBalance)
        .join(Product, Product.id == StockBalance.product_id)
        .where(Product.is_active.is_(True), Product.is_stock_item.is_(True), StockBalance.quantity <= 0)
    ) or 0
    valuation = inventory.valuation(db)
    return {
        "open_purchase_orders": int(open_orders),
        "bills_to_post": int(bills_to_post),
        "invoices_to_post": int(invoices_to_post),
        "documents_to_review": int(review_queue),
        "stock_lines_at_zero": int(no_stock),
        "stock_value": cd.money(Decimal(valuation["total_value"])),
    }
