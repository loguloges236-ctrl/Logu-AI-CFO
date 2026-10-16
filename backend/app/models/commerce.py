"""Phase 3 ORM models: catalogue, inventory, purchasing, sales and document processing.

The explicit migration alembic/versions/20261008_0003_commerce.py defines the schema. This metadata must match it;
tests/unit/test_commerce_migration_static.py compares table and column names with the migration text.
Quantities are NUMERIC(18,3), unit costs and rates NUMERIC(18,4), money NUMERIC(18,2). Floats are never used.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base

QTY = Numeric(18, 3)
RATE = Numeric(18, 4)
MONEY = Numeric(18, 2)
PERCENT = Numeric(5, 2)
TAX_RATE = Numeric(7, 4)


class _Stamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _uid(primary: bool = False) -> Any:
    return mapped_column(Uuid(as_uuid=True), primary_key=primary, default=uuid.uuid4 if primary else None)


class ProductCategory(_Stamps, Base):
    __tablename__ = "product_categories"

    id: Mapped[uuid.UUID] = _uid(primary=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500))


class Product(_Stamps, Base):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_name", "name"), Index("ix_products_category", "category_id"))

    id: Mapped[uuid.UUID] = _uid(primary=True)
    sku: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_categories.id", ondelete="RESTRICT"))
    unit_of_measure: Mapped[str] = mapped_column(String(10), nullable=False)
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    tax_rate: Mapped[Decimal | None] = mapped_column(TAX_RATE)
    purchase_price: Mapped[Decimal | None] = mapped_column(MONEY)
    selling_price: Mapped[Decimal | None] = mapped_column(MONEY)
    is_stock_item: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class Party(_Stamps, Base):
    __tablename__ = "parties"
    __table_args__ = (Index("ix_parties_name", "name"), Index("ix_parties_kind_active", "kind", "is_active"))

    id: Mapped[uuid.UUID] = _uid(primary=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class Warehouse(_Stamps, Base):
    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = _uid(primary=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    allow_negative_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class StockLocation(_Stamps, Base):
    __tablename__ = "stock_locations"
    __table_args__ = (Index("ix_stock_locations_warehouse", "warehouse_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class StockBalance(Base):
    """Derived running balance per product and location. Every change is paired with a StockMovement."""

    __tablename__ = "stock_balances"
    __table_args__ = (Index("ix_stock_balances_location", "location_id"),)

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_locations.id", ondelete="RESTRICT"), primary_key=True
    )
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal("0"), server_default="0")
    average_cost: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("0"), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StockMovement(Base):
    """Append-only. Database triggers refuse UPDATE, DELETE and TRUNCATE."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_product_created", "product_id", "created_at"),
        Index("ix_stock_movements_location", "location_id"),
        Index("ix_stock_movements_source", "source_type", "source_id"),
        Index("ix_stock_movements_transfer", "transfer_group_id"),
        Index("ix_stock_movements_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uid(primary=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_locations.id", ondelete="RESTRICT"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(RATE)
    balance_after: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    average_cost_after: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="manual", server_default="manual")
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CommerceAccountMapping(Base):
    __tablename__ = "commerce_account_mappings"

    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class PurchaseRequest(_Stamps, Base):
    __tablename__ = "purchase_requests"

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    request_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    requested_by: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class PurchaseRequestLine(_Stamps, Base):
    __tablename__ = "purchase_request_lines"

    id: Mapped[uuid.UUID] = _uid(primary=True)
    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="RESTRICT"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)


class PurchaseOrder(_Stamps, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_purchase_orders_party", "party_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_requests.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class _DocumentLine(_Stamps):
    """Shared amount columns for priced document lines. All amounts are computed server-side."""

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    @declared_attr
    def product_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805  (declarative mixin convention)
        return mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))

    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False, default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(TAX_RATE, nullable=False, default=Decimal("0"))
    line_subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class PurchaseOrderLine(_DocumentLine, Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (Index("ix_purchase_order_lines_product", "product_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False)


class GoodsReceipt(_Stamps, Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (
        Index("ix_goods_receipts_po", "purchase_order_id"),
        Index("ix_goods_receipts_date", "receipt_date"),
        Index("ix_goods_receipts_warehouse", "warehouse_id"),
    )

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_locations.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="posted", server_default="posted")
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class GoodsReceiptLine(_Stamps, Base):
    __tablename__ = "goods_receipt_lines"
    __table_args__ = (
        Index("ix_goods_receipt_lines_po_line", "purchase_order_line_id"),
        Index("ix_goods_receipt_lines_product", "product_id"),
    )

    id: Mapped[uuid.UUID] = _uid(primary=True)
    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goods_receipts.id", ondelete="RESTRICT"), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    purchase_order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(RATE, nullable=False)


class PurchaseBill(_Stamps, Base):
    __tablename__ = "purchase_bills"
    __table_args__ = (
        Index("uq_purchase_bills_supplier_invoice", "party_id", text("lower(supplier_invoice_number)"), unique=True),
        Index("ix_purchase_bills_party", "party_id"),
        Index("ix_purchase_bills_status", "status"),
        Index("ix_purchase_bills_date", "bill_date"),
        Index("ix_purchase_bills_po", "purchase_order_id"),
    )

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    supplier_invoice_number: Mapped[str] = mapped_column(String(60), nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    payment_date: Mapped[date | None] = mapped_column(Date)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class PurchaseBillLine(_DocumentLine, Base):
    __tablename__ = "purchase_bill_lines"
    __table_args__ = (Index("ix_purchase_bill_lines_grn_line", "goods_receipt_line_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    purchase_bill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_bills.id", ondelete="RESTRICT"), nullable=False)
    goods_receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goods_receipt_lines.id", ondelete="RESTRICT"))
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))


class Quotation(_Stamps, Base):
    __tablename__ = "quotations"
    __table_args__ = (Index("ix_quotations_party", "party_id"), Index("ix_quotations_status", "status"))

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    quotation_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class QuotationLine(_DocumentLine, Base):
    __tablename__ = "quotation_lines"

    id: Mapped[uuid.UUID] = _uid(primary=True)
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=False)


class SalesOrder(_Stamps, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (Index("ix_sales_orders_party", "party_id"), Index("ix_sales_orders_status", "status"))

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotations.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class SalesOrderLine(_DocumentLine, Base):
    __tablename__ = "sales_order_lines"
    __table_args__ = (Index("ix_sales_order_lines_product", "product_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False)
    invoiced_quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal("0"), server_default="0")


class SalesInvoice(_Stamps, Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (
        Index("ix_sales_invoices_party", "party_id"),
        Index("ix_sales_invoices_order", "sales_order_id"),
        Index("ix_sales_invoices_status", "status"),
        Index("ix_sales_invoices_date", "invoice_date"),
    )

    id: Mapped[uuid.UUID] = _uid(primary=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_locations.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    cost_of_goods_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    payment_received_on: Mapped[date | None] = mapped_column(Date)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))


class SalesInvoiceLine(_DocumentLine, Base):
    __tablename__ = "sales_invoice_lines"
    __table_args__ = (Index("ix_sales_invoice_lines_order_line", "sales_order_line_id"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    sales_invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False)
    sales_order_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(RATE)
    cost_amount: Mapped[Decimal | None] = mapped_column(MONEY)


class DocumentProcessingJob(_Stamps, Base):
    __tablename__ = "document_processing_jobs"
    __table_args__ = (Index("ix_document_jobs_status", "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uid(primary=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    classification: Mapped[str | None] = mapped_column(String(30))
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    provider_name: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    raw_extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    current_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    issues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    review_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    approved_document_type: Mapped[str | None] = mapped_column(String(40))
    approved_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
