"""Request schemas for Phase 3. Every model forbids unknown fields. Money, rates and quantities are decimal
strings (never JSON numbers); the service layer parses them strictly. Dates are ISO dates."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_LINES = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- catalogue


class CategoryIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class ProductIn(_Strict):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    category_id: UUID | None = None
    unit_of_measure: str = Field(min_length=1, max_length=10)
    hsn_sac: str | None = Field(default=None, max_length=8)
    tax_rate: str | None = Field(default=None, max_length=8)
    purchase_price: str | None = Field(default=None, max_length=24)
    selling_price: str | None = Field(default=None, max_length=24)
    barcode: str | None = Field(default=None, max_length=64)
    is_stock_item: bool = True


class ProductUpdate(_Strict):
    sku: str | None = Field(default=None, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    category_id: UUID | None = None
    unit_of_measure: str | None = Field(default=None, max_length=10)
    hsn_sac: str | None = Field(default=None, max_length=8)
    tax_rate: str | None = Field(default=None, max_length=8)
    purchase_price: str | None = Field(default=None, max_length=24)
    selling_price: str | None = Field(default=None, max_length=24)
    barcode: str | None = Field(default=None, max_length=64)
    is_stock_item: bool | None = None
    is_active: bool | None = None


class PartyIn(_Strict):
    kind: Literal["supplier", "customer", "both"]
    name: str = Field(min_length=1, max_length=160)
    tax_id: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)


class PartyUpdate(_Strict):
    kind: Literal["supplier", "customer", "both"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    tax_id: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    is_active: bool | None = None


class WarehouseIn(_Strict):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    allow_negative_stock: bool = False


class WarehouseUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    allow_negative_stock: bool | None = None
    is_active: bool | None = None


class LocationIn(_Strict):
    warehouse_id: UUID
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)


class LocationUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class MappingIn(_Strict):
    account_id: UUID


# ---------------------------------------------------------------- stock


class StockInIn(_Strict):
    product_id: UUID
    location_id: UUID
    quantity: str = Field(max_length=24)
    unit_cost: str = Field(max_length=24)
    reason: str | None = Field(default=None, max_length=255)


class StockOutIn(_Strict):
    product_id: UUID
    location_id: UUID
    quantity: str = Field(max_length=24)
    reason: str | None = Field(default=None, max_length=255)


class CustomerReturnIn(_Strict):
    product_id: UUID
    location_id: UUID
    quantity: str = Field(max_length=24)
    unit_cost: str | None = Field(default=None, max_length=24)
    reason: str | None = Field(default=None, max_length=255)


class AdjustmentIn(_Strict):
    product_id: UUID
    location_id: UUID
    direction: Literal["increase", "decrease"]
    quantity: str = Field(max_length=24)
    unit_cost: str | None = Field(default=None, max_length=24)
    reason: str = Field(min_length=1, max_length=255)


class TransferIn(_Strict):
    product_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    quantity: str = Field(max_length=24)
    reason: str | None = Field(default=None, max_length=255)


class ReconcileIn(_Strict):
    product_id: UUID
    location_id: UUID
    counted_quantity: str = Field(max_length=24)
    unit_cost: str | None = Field(default=None, max_length=24)
    reason: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------- documents (purchasing and sales)


class DocLineIn(_Strict):
    product_id: UUID | None = None
    description: str | None = Field(default=None, max_length=255)
    quantity: str = Field(max_length=24)
    rate: str | None = Field(default=None, max_length=24)
    discount_percent: str | None = Field(default=None, max_length=8)
    tax_rate: str | None = Field(default=None, max_length=8)
    expense_account_id: UUID | None = None
    # Three-way match: a stock line on a bill must name the goods-receipt line it bills against.
    goods_receipt_line_id: UUID | None = None


class PurchaseRequestIn(_Strict):
    request_date: date
    requested_by: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    lines: list[DocLineIn] = Field(min_length=1, max_length=MAX_LINES)


class PurchaseOrderIn(_Strict):
    party_id: UUID
    order_date: date
    expected_date: date | None = None
    purchase_request_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=500)
    lines: list[DocLineIn] = Field(min_length=1, max_length=MAX_LINES)


class ReceiptLineIn(_Strict):
    purchase_order_line_id: UUID
    quantity: str = Field(max_length=24)


class GoodsReceiptIn(_Strict):
    purchase_order_id: UUID
    receipt_date: date
    warehouse_id: UUID
    location_id: UUID
    notes: str | None = Field(default=None, max_length=500)
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=MAX_LINES)


class PurchaseBillIn(_Strict):
    party_id: UUID
    supplier_invoice_number: str = Field(min_length=1, max_length=60)
    bill_date: date
    due_date: date | None = None
    purchase_order_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=500)
    lines: list[DocLineIn] = Field(min_length=1, max_length=MAX_LINES)


class PaymentReferenceIn(_Strict):
    reference: str = Field(min_length=1, max_length=120)
    paid_on: date


class QuotationIn(_Strict):
    party_id: UUID
    quotation_date: date
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    lines: list[DocLineIn] = Field(min_length=1, max_length=MAX_LINES)


class QuotationStatusIn(_Strict):
    status: Literal["sent", "accepted", "rejected", "expired"]


class SalesOrderIn(_Strict):
    party_id: UUID
    order_date: date
    notes: str | None = Field(default=None, max_length=500)
    lines: list[DocLineIn] = Field(min_length=1, max_length=MAX_LINES)


class SalesOrderStatusIn(_Strict):
    status: Literal["confirmed", "cancelled"]


class InvoiceLineIn(_Strict):
    sales_order_line_id: UUID
    quantity: str = Field(max_length=24)


class SalesInvoiceIn(_Strict):
    sales_order_id: UUID
    invoice_date: date
    due_date: date | None = None
    warehouse_id: UUID | None = None
    location_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=500)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=MAX_LINES)


# ---------------------------------------------------------------- document processing


class ProcessingJobIn(_Strict):
    document_id: UUID


class ProcessingCorrectionIn(_Strict):
    document_type: str | None = Field(default=None, max_length=30)
    party_id: UUID | None = None
    party_name: str | None = Field(default=None, max_length=160)
    reference_number: str | None = Field(default=None, max_length=60)
    document_date: str | None = Field(default=None, max_length=10)
    due_date: str | None = Field(default=None, max_length=10)
    grand_total: str | None = Field(default=None, max_length=24)
    lines: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_LINES)
    notes: str | None = Field(default=None, max_length=500)


class ProcessingApproveIn(_Strict):
    acknowledge_warnings: bool = False


class ProcessingRejectIn(_Strict):
    reason: str = Field(min_length=1, max_length=120)
