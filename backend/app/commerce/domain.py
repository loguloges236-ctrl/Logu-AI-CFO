"""Pure business rules for products, inventory, purchasing and sales.

No database access happens here. Services load rows, call these functions, then write. Every amount is
`Decimal`; every string input is parsed by a strict regex (no floats, no scientific notation, no signs
unless stated). Rounding is ROUND_HALF_UP at the precision of the column that stores the value:
money 2 dp, quantity 3 dp, unit cost 4 dp.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import NamedTuple

Q2 = Decimal("0.01")
Q3 = Decimal("0.001")
Q4 = Decimal("0.0001")

MAX_LINES_PER_DOCUMENT = 200
MAX_NAME = 160


class CommerceRuleViolation(Exception):
    """A business rule was broken. `code` is stable and safe to show to the API client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _violation(code: str, message: str) -> CommerceRuleViolation:
    return CommerceRuleViolation(code, message)


def q2(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def q3(value: Decimal) -> Decimal:
    return value.quantize(Q3, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- parsing


_QTY = re.compile(r"^\d{1,15}(\.\d{1,3})?$")
_RATE = re.compile(r"^\d{1,14}(\.\d{1,4})?$")
_AMOUNT = re.compile(r"^\d{1,16}(\.\d{1,2})?$")
_PERCENT = re.compile(r"^\d{1,3}(\.\d{1,2})?$")
_SKU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,59}$")
_BARCODE = re.compile(r"^[0-9A-Za-z-]{4,64}$")
_HSN_SAC = re.compile(r"^[0-9]{4,8}$")
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UNITS_OF_MEASURE = ("pcs", "kg", "g", "l", "ml", "m", "box", "pack", "pair", "set", "hr")


def parse_quantity(text: object, field: str = "quantity") -> Decimal:
    """Positive quantity, at most 3 decimal places."""
    if not isinstance(text, str) or not _QTY.match(text):
        raise _violation("invalid_quantity", f"{field} must be a positive number with up to 3 decimal places")
    value = Decimal(text)
    if value <= 0:
        raise _violation("invalid_quantity", f"{field} must be greater than zero")
    return value


def parse_count(text: object, field: str = "counted quantity") -> Decimal:
    """Non-negative quantity (a physical count may be zero), at most 3 decimal places."""
    if not isinstance(text, str) or not _QTY.match(text):
        raise _violation("invalid_quantity", f"{field} must be zero or a positive number with up to 3 decimal places")
    return Decimal(text)


def parse_rate(text: object, field: str = "rate") -> Decimal:
    """Non-negative unit price, at most 4 decimal places."""
    if not isinstance(text, str) or not _RATE.match(text):
        raise _violation("invalid_amount", f"{field} must be a non-negative number with up to 4 decimal places")
    return Decimal(text)


def parse_amount(text: object, field: str = "amount") -> Decimal:
    """Non-negative money amount, at most 2 decimal places."""
    if not isinstance(text, str) or not _AMOUNT.match(text):
        raise _violation("invalid_amount", f"{field} must be a non-negative amount with up to 2 decimal places")
    return Decimal(text)


def parse_percent(text: object, field: str = "percent") -> Decimal:
    if text is None or text == "":
        return Decimal("0")
    if not isinstance(text, str) or not _PERCENT.match(text):
        raise _violation("invalid_percent", f"{field} must be between 0 and 100")
    value = Decimal(text)
    if value > 100:
        raise _violation("invalid_percent", f"{field} must be between 0 and 100")
    return value


def parse_iso_date(text: object, field: str) -> str:
    if not isinstance(text, str) or not _ISO_DATE.match(text):
        raise _violation("invalid_date", f"{field} must be a date in YYYY-MM-DD form")
    return text


def validate_sku(sku: str) -> str:
    value = sku.strip()
    if not _SKU.match(value):
        raise _violation("invalid_sku", "SKU must be 1-60 letters, digits, dots, dashes, slashes or underscores")
    return value


def validate_barcode(barcode: str | None) -> str | None:
    if barcode is None or barcode.strip() == "":
        return None
    value = barcode.strip()
    if not _BARCODE.match(value):
        raise _violation("invalid_barcode", "Barcode must be 4-64 letters, digits or dashes")
    return value


def validate_hsn_sac(code: str | None) -> str | None:
    if code is None or code.strip() == "":
        return None
    value = code.strip()
    if not _HSN_SAC.match(value):
        raise _violation("invalid_hsn_sac", "HSN/SAC code must be 4 to 8 digits")
    return value


def validate_code(code: str, field: str = "code") -> str:
    value = code.strip().upper()
    if not _CODE.match(value):
        raise _violation("invalid_code", f"{field} must be 1-20 letters, digits, dashes or underscores")
    return value


def validate_uom(uom: str) -> str:
    if uom not in UNITS_OF_MEASURE:
        raise _violation("invalid_unit", "Unknown unit of measure")
    return uom


def validate_name(name: str, field: str = "name") -> str:
    value = name.strip()
    if not value or len(value) > MAX_NAME:
        raise _violation("invalid_name", f"{field} is required and must be at most {MAX_NAME} characters")
    return value


def validate_line_count(count: int) -> None:
    if count < 1:
        raise _violation("too_few_lines", "A document needs at least one line")
    if count > MAX_LINES_PER_DOCUMENT:
        raise _violation("too_many_lines", "A document has too many lines")


# ---------------------------------------------------------------- document totals


class LineAmounts(NamedTuple):
    subtotal: Decimal
    discount: Decimal
    taxable: Decimal
    tax: Decimal
    total: Decimal


def line_amounts(quantity: Decimal, rate: Decimal, discount_percent: Decimal, tax_percent: Decimal) -> LineAmounts:
    """Server-side arithmetic for one line. Client totals are never trusted."""
    subtotal = q2(quantity * rate)
    discount = q2(subtotal * discount_percent / 100)
    taxable = subtotal - discount
    tax = q2(taxable * tax_percent / 100)
    return LineAmounts(subtotal, discount, taxable, tax, taxable + tax)


class DocumentTotals(NamedTuple):
    subtotal: Decimal
    discount: Decimal
    taxable: Decimal
    tax: Decimal
    grand_total: Decimal


def document_totals(lines: Iterable[LineAmounts]) -> DocumentTotals:
    subtotal = discount = taxable = tax = Decimal("0")
    for line in lines:
        subtotal += line.subtotal
        discount += line.discount
        taxable += line.taxable
        tax += line.tax
    return DocumentTotals(q2(subtotal), q2(discount), q2(taxable), q2(tax), q2(taxable + tax))


def money(value: Decimal) -> str:
    return f"{q2(value):.2f}"


def quantity_text(value: Decimal) -> str:
    return f"{q3(value):.3f}"


def cost_text(value: Decimal) -> str:
    return f"{q4(value):.4f}"


# ---------------------------------------------------------------- inventory


class MovementType(StrEnum):
    STOCK_IN = "stock_in"  # manual receipt or opening stock
    GRN_IN = "grn_in"  # goods receipt against a purchase order
    RETURN_IN = "return_in"  # goods returned by a customer (or a voided invoice)
    TRANSFER_IN = "transfer_in"
    ADJUSTMENT_IN = "adjustment_in"  # physical count found more stock
    STOCK_OUT = "stock_out"  # manual issue
    SALE_OUT = "sale_out"  # dispatched on a sales invoice
    DAMAGE_OUT = "damage_out"  # written off as damaged
    RETURN_OUT = "return_out"  # returned to a supplier
    TRANSFER_OUT = "transfer_out"
    ADJUSTMENT_OUT = "adjustment_out"  # physical count found less stock


IN_TYPES = frozenset(
    {MovementType.STOCK_IN, MovementType.GRN_IN, MovementType.RETURN_IN, MovementType.TRANSFER_IN,
     MovementType.ADJUSTMENT_IN}
)
OUT_TYPES = frozenset(set(MovementType) - IN_TYPES)
TRANSFER_TYPES = frozenset({MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT})


class StockDecision(NamedTuple):
    new_quantity: Decimal
    new_average_cost: Decimal
    applied_unit_cost: Decimal


def apply_stock_change(
    *,
    balance_quantity: Decimal,
    average_cost: Decimal,
    quantity_delta: Decimal,
    movement_type: MovementType,
    unit_cost: Decimal | None,
    allow_negative: bool,
) -> StockDecision:
    """Weighted-average valuation with a hard floor at zero unless the warehouse allows negative stock.

    Inbound movements must carry a unit cost and move the average. Outbound movements are valued at the
    current average and leave it unchanged.
    """
    if quantity_delta == 0:
        raise _violation("invalid_quantity", "A stock movement must change the quantity")
    if (movement_type in IN_TYPES) != (quantity_delta > 0):
        raise _violation("invalid_direction", "The quantity sign does not match the movement type")
    new_quantity = q3(balance_quantity + quantity_delta)
    if new_quantity < 0 and not allow_negative:
        raise _violation(
            "negative_stock",
            f"Not enough stock. Available {quantity_text(balance_quantity)}, requested {quantity_text(-quantity_delta)}.",
        )
    if quantity_delta > 0:
        if unit_cost is None or unit_cost < 0:
            raise _violation("unit_cost_required", "Inbound stock needs a non-negative unit cost")
        if balance_quantity <= 0:
            new_average = q4(unit_cost)
        else:
            new_average = q4(
                (balance_quantity * average_cost + quantity_delta * unit_cost) / (balance_quantity + quantity_delta)
            )
        return StockDecision(new_quantity, new_average, q4(unit_cost))
    return StockDecision(new_quantity, q4(average_cost), q4(average_cost))


def check_transfer(
    *, from_location: str, to_location: str, quantity_out: Decimal, quantity_in: Decimal
) -> None:
    if from_location == to_location:
        raise _violation("same_location", "A transfer needs two different locations")
    if quantity_out != quantity_in:
        raise _violation("transfer_unbalanced", "The quantity leaving and arriving must be equal")


def reconciliation_delta(book_quantity: Decimal, counted_quantity: Decimal) -> Decimal:
    """Difference that a physical count requires. Zero means no movement is written."""
    if counted_quantity < 0:
        raise _violation("invalid_quantity", "Counted quantity cannot be negative")
    return q3(counted_quantity - book_quantity)


# ---------------------------------------------------------------- purchasing and sales quantities


def check_receipt_quantity(*, ordered: Decimal, received_before: Decimal, new_quantity: Decimal, allow_over: bool) -> None:
    if received_before + new_quantity > ordered and not allow_over:
        raise _violation(
            "over_receipt",
            f"Receipt exceeds the ordered quantity. Remaining {quantity_text(ordered - received_before)}.",
        )


def check_bill_quantity(*, received: Decimal, billed_before: Decimal, new_quantity: Decimal) -> None:
    if billed_before + new_quantity > received:
        raise _violation(
            "bill_exceeds_receipt",
            f"A bill cannot exceed the goods received. Unbilled {quantity_text(received - billed_before)}.",
        )


def check_invoice_quantity(
    *,
    ordered: Decimal,
    invoiced_before: Decimal,
    new_quantity: Decimal,
    allow_over: bool,
    allow_partial: bool,
) -> None:
    total = invoiced_before + new_quantity
    if total > ordered and not allow_over:
        raise _violation(
            "over_invoice",
            f"Invoice exceeds the ordered quantity. Remaining {quantity_text(ordered - invoiced_before)}.",
        )
    if not allow_partial and total < ordered:
        raise _violation("partial_invoice_disabled", "Partial invoicing is disabled. Invoice the full quantity.")


def po_status_after_receipt(ordered_total: Decimal, received_total: Decimal) -> str:
    if received_total <= 0:
        return "approved"
    if received_total < ordered_total:
        return "partially_received"
    return "received"


def so_status_after_invoicing(ordered_total: Decimal, invoiced_total: Decimal) -> str:
    if invoiced_total <= 0:
        return "confirmed"
    if invoiced_total < ordered_total:
        return "partially_invoiced"
    return "invoiced"


# ---------------------------------------------------------------- status transitions

TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "purchase_request": {
        "draft": frozenset({"approved", "rejected"}),
        "approved": frozenset({"ordered"}),
    },
    "purchase_order": {
        "draft": frozenset({"approved", "cancelled"}),
        "approved": frozenset({"cancelled", "partially_received", "received"}),
        "partially_received": frozenset({"received"}),
    },
    "quotation": {
        "draft": frozenset({"sent", "rejected"}),
        "sent": frozenset({"accepted", "rejected", "expired"}),
        "accepted": frozenset({"converted"}),
    },
    "sales_order": {
        "draft": frozenset({"confirmed", "cancelled"}),
        "confirmed": frozenset({"partially_invoiced", "invoiced", "cancelled"}),
        "partially_invoiced": frozenset({"invoiced"}),
    },
    "purchase_bill": {"draft": frozenset({"posted"}), "posted": frozenset({"voided"})},
    "sales_invoice": {"draft": frozenset({"posted"}), "posted": frozenset({"voided"})},
}


def require_transition(kind: str, current: str, target: str) -> None:
    allowed = TRANSITIONS.get(kind, {}).get(current, frozenset())
    if target not in allowed:
        raise _violation("invalid_transition", f"A {kind.replace('_', ' ')} cannot move from {current} to {target}")


# ---------------------------------------------------------------- account roles

ACCOUNT_ROLE_CATEGORIES: dict[str, frozenset[str]] = {
    "inventory": frozenset({"asset"}),
    "accounts_receivable": frozenset({"asset"}),
    "input_tax": frozenset({"asset"}),
    "accounts_payable": frozenset({"liability"}),
    "output_tax": frozenset({"liability"}),
    "sales_revenue": frozenset({"income"}),
    "cost_of_goods_sold": frozenset({"expense"}),
    "default_expense": frozenset({"expense"}),
}
REQUIRED_ROLES_FOR_PURCHASE_BILL = ("accounts_payable", "inventory", "input_tax")
REQUIRED_ROLES_FOR_SALES_INVOICE = ("accounts_receivable", "sales_revenue", "output_tax")
REQUIRED_ROLES_FOR_COGS = ("cost_of_goods_sold", "inventory")


def missing_roles(required: Iterable[str], configured: dict[str, str]) -> list[str]:
    return sorted(role for role in required if role not in configured)


PARTY_KINDS = frozenset({"supplier", "customer", "both"})


def party_allowed(kind: str, role: str) -> bool:
    """`role` is 'supplier' or 'customer'. A party marked 'both' can act in either role."""
    return kind == role or kind == "both"
