"""Line pricing shared by quotations, sales orders, purchase orders and bills.

Server-side arithmetic only. Client-supplied totals are never stored. Product tax defaults apply when a line
omits its tax rate; a non-stock service item may appear on a document with its own description.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session as DBSession

from app.commerce import domain as cd
from app.services import commerce_catalog as catalog
from app.services.commerce_common import rule


@dataclass(frozen=True)
class PricedLine:
    product_id: UUID | None
    description: str
    quantity: Decimal
    rate: Decimal
    discount_percent: Decimal
    tax_rate: Decimal
    amounts: cd.LineAmounts


def price_line(db: DBSession, raw: dict[str, Any], *, rate_default: str = "product_purchase") -> PricedLine:
    """Turn one raw line into priced, validated values. `rate_default` picks the product price when no rate is sent."""
    product = None
    product_id = raw.get("product_id")
    if product_id is not None:
        product = catalog.require_active_product(db, product_id, stock=False)
    description = raw.get("description")
    if product is not None:
        description = (description or "").strip() or product.name
    if not description:
        raise rule("line_unidentified", "Every line needs a product or a description")
    description = cd.validate_name(description, "Line description")

    quantity = cd.parse_quantity(raw.get("quantity"))
    rate_text = raw.get("rate")
    if rate_text in (None, "") and product is not None:
        fallback = product.purchase_price if rate_default == "product_purchase" else product.selling_price
        if fallback is None:
            raise rule("rate_required", f"Enter a rate for {product.sku}")
        rate = Decimal(str(fallback))
    else:
        rate = cd.parse_rate(rate_text, "rate")
    discount = cd.parse_percent(raw.get("discount_percent"), "discount_percent")

    tax_text = raw.get("tax_rate")
    if tax_text in (None, ""):
        tax = Decimal(str(product.tax_rate)) if product is not None and product.tax_rate is not None else Decimal("0")
    else:
        tax = cd.parse_percent(tax_text, "tax_rate")

    return PricedLine(
        product_id=product.id if product is not None else None,
        description=description,
        quantity=quantity,
        rate=rate,
        discount_percent=discount,
        tax_rate=tax,
        amounts=cd.line_amounts(quantity, rate, discount, tax),
    )


def price_lines(db: DBSession, lines: list[dict[str, Any]], *, rate_default: str = "product_purchase") -> list[PricedLine]:
    cd.validate_line_count(len(lines))
    return [price_line(db, raw, rate_default=rate_default) for raw in lines]


def apply_totals(header: Any, priced: list[PricedLine]) -> None:
    """Write the header totals from the priced lines."""
    totals = cd.document_totals(line.amounts for line in priced)
    header.subtotal = totals.subtotal
    header.discount_total = totals.discount
    header.taxable_total = totals.taxable
    header.tax_total = totals.tax
    header.grand_total = totals.grand_total
