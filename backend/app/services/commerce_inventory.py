"""Inventory and store: every quantity change is a StockMovement. Balances are derived and never edited directly.

Stock rules enforced here (and by the database for the negative-stock floor and transfer balance):
- only active stock items at active locations in active warehouses can move;
- quantities are positive numbers; the direction is set by the movement type;
- stock cannot go negative unless the warehouse explicitly allows it;
- inbound stock needs a unit cost; outbound stock is valued at the current weighted average;
- a transfer writes two legs in one transaction, and they must balance;
- a physical count writes only the difference, and nothing when the count matches the book.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DBSession

from app.commerce import domain as cd
from app.core.clock import utcnow
from app.models import Product, StockBalance, StockLocation, StockMovement, Warehouse
from app.services import commerce_catalog as catalog
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, flush, rule, unit_of_work


def _balance_row(db: DBSession, product_id: UUID, location_id: UUID) -> StockBalance:
    """Lock (creating if needed) the balance row. Concurrent movements on one product serialise here."""
    db.execute(
        pg_insert(StockBalance)
        .values(product_id=product_id, location_id=location_id, quantity=Decimal("0"), average_cost=Decimal("0"))
        .on_conflict_do_nothing(index_elements=["product_id", "location_id"])
    )
    row = db.scalar(
        select(StockBalance)
        .where(StockBalance.product_id == product_id, StockBalance.location_id == location_id)
        .with_for_update()
    )
    if row is None:  # pragma: no cover - the insert above guarantees the row
        raise rule("stock_unavailable", "Stock balance could not be opened")
    return row


def move_stock(
    db: DBSession,
    *,
    owner_id: UUID,
    product_id: UUID,
    location_id: UUID,
    allow_negative: bool,
    movement_type: cd.MovementType,
    quantity_delta: Decimal,
    unit_cost: Decimal | None,
    source_type: str,
    source_id: UUID | None = None,
    source_line_id: UUID | None = None,
    transfer_group_id: UUID | None = None,
    reason: str | None = None,
    default_inbound_cost: bool = False,
) -> StockMovement:
    """Write one movement and update the balance. Never commits: the caller owns the transaction."""
    balance = _balance_row(db, product_id, location_id)
    cost = unit_cost
    if cost is None and default_inbound_cost and quantity_delta > 0:
        cost = balance.average_cost  # e.g. a return with no recorded cost is valued at the current average
    decision = cd.apply_stock_change(
        balance_quantity=balance.quantity,
        average_cost=balance.average_cost,
        quantity_delta=quantity_delta,
        movement_type=movement_type,
        unit_cost=cost,
        allow_negative=allow_negative,
    )
    balance.quantity = decision.new_quantity
    balance.average_cost = decision.new_average_cost
    balance.updated_at = utcnow()
    movement = StockMovement(
        id=uuid.uuid4(),
        product_id=product_id,
        location_id=location_id,
        movement_type=movement_type.value,
        quantity_delta=quantity_delta,
        unit_cost=decision.applied_unit_cost,
        balance_after=decision.new_quantity,
        average_cost_after=decision.new_average_cost,
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
        transfer_group_id=transfer_group_id,
        reason=(reason or None),
        created_by_owner_id=owner_id,
    )
    db.add(movement)
    flush(db)  # the database re-checks sign, append-only and the negative-stock floor here
    return movement


def _stock_target(db: DBSession, product_id: UUID, location_id: UUID) -> tuple[Product, StockLocation, Warehouse]:
    product = catalog.require_active_product(db, product_id)
    location, warehouse = catalog.require_location(db, location_id)
    return product, location, warehouse


def _record(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    action: str,
    movement: StockMovement,
    product: Product,
) -> None:
    audit(
        db,
        request,
        auth,
        action,
        "stock_movement",
        str(movement.id),
        {"sku": product.sku, "type": movement.movement_type, "quantity": cd.quantity_text(movement.quantity_delta)},
    )


def receive_stock(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    quantity: str, unit_cost: str, reason: str | None,
) -> StockMovement:
    qty = cd.parse_quantity(quantity)
    cost = cd.parse_rate(unit_cost, "unit_cost")
    product, location, warehouse = _stock_target(db, product_id, location_id)
    with unit_of_work(db):
        movement = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=location.id,
            allow_negative=warehouse.allow_negative_stock, movement_type=cd.MovementType.STOCK_IN,
            quantity_delta=qty, unit_cost=cost, source_type="manual", reason=reason,
        )
        _record(db, request, auth, "stock.in", movement, product)
    return movement


def issue_stock(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    quantity: str, reason: str | None,
) -> StockMovement:
    return _outbound(db, request, auth, product_id, location_id, quantity, reason, cd.MovementType.STOCK_OUT, "stock.out")


def damage_stock(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    quantity: str, reason: str | None,
) -> StockMovement:
    return _outbound(db, request, auth, product_id, location_id, quantity, reason, cd.MovementType.DAMAGE_OUT, "stock.damaged")


def return_to_supplier(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    quantity: str, reason: str | None,
) -> StockMovement:
    return _outbound(db, request, auth, product_id, location_id, quantity, reason, cd.MovementType.RETURN_OUT, "stock.returned_to_supplier")


def _outbound(
    db: DBSession, request: Request, auth: AuthenticatedSession, product_id: UUID, location_id: UUID,
    quantity: str, reason: str | None, movement_type: cd.MovementType, action: str,
) -> StockMovement:
    qty = cd.parse_quantity(quantity)
    product, location, warehouse = _stock_target(db, product_id, location_id)
    with unit_of_work(db):
        movement = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=location.id,
            allow_negative=warehouse.allow_negative_stock, movement_type=movement_type,
            quantity_delta=-qty, unit_cost=None, source_type="manual", reason=reason,
        )
        _record(db, request, auth, action, movement, product)
    return movement


def customer_return(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    quantity: str, unit_cost: str | None, reason: str | None,
) -> StockMovement:
    """Goods returned by a customer, valued at `unit_cost` or the current average when none is given."""
    qty = cd.parse_quantity(quantity)
    cost = None if unit_cost in (None, "") else cd.parse_rate(unit_cost, "unit_cost")
    product, location, warehouse = _stock_target(db, product_id, location_id)
    with unit_of_work(db):
        movement = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=location.id,
            allow_negative=warehouse.allow_negative_stock, movement_type=cd.MovementType.RETURN_IN,
            quantity_delta=qty, unit_cost=cost, source_type="manual", reason=reason, default_inbound_cost=True,
        )
        _record(db, request, auth, "stock.customer_return", movement, product)
    return movement


def adjust_stock(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    direction: str, quantity: str, unit_cost: str | None, reason: str,
) -> StockMovement:
    if direction not in ("increase", "decrease"):
        raise rule("invalid_direction", "Adjustment direction must be increase or decrease")
    if not reason or not reason.strip():
        raise rule("reason_required", "An adjustment needs a reason")
    qty = cd.parse_quantity(quantity)
    cost = None if unit_cost in (None, "") else cd.parse_rate(unit_cost, "unit_cost")
    product, location, warehouse = _stock_target(db, product_id, location_id)
    increase = direction == "increase"
    with unit_of_work(db):
        movement = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=location.id,
            allow_negative=warehouse.allow_negative_stock,
            movement_type=cd.MovementType.ADJUSTMENT_IN if increase else cd.MovementType.ADJUSTMENT_OUT,
            quantity_delta=qty if increase else -qty, unit_cost=cost, source_type="manual", reason=reason,
            default_inbound_cost=increase,
        )
        _record(db, request, auth, "stock.adjusted", movement, product)
    return movement


def transfer_stock(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID,
    from_location_id: UUID, to_location_id: UUID, quantity: str, reason: str | None,
) -> tuple[StockMovement, StockMovement]:
    qty = cd.parse_quantity(quantity)
    product, source, source_wh = _stock_target(db, product_id, from_location_id)
    destination, destination_wh = catalog.require_location(db, to_location_id)
    cd.check_transfer(
        from_location=str(from_location_id), to_location=str(to_location_id), quantity_out=qty, quantity_in=qty
    )
    group = uuid.uuid4()
    with unit_of_work(db):
        leg_out = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=source.id,
            allow_negative=source_wh.allow_negative_stock, movement_type=cd.MovementType.TRANSFER_OUT,
            quantity_delta=-qty, unit_cost=None, source_type="transfer", transfer_group_id=group, reason=reason,
        )
        leg_in = move_stock(
            db, owner_id=auth.owner.id, product_id=product.id, location_id=destination.id,
            allow_negative=destination_wh.allow_negative_stock, movement_type=cd.MovementType.TRANSFER_IN,
            quantity_delta=qty, unit_cost=leg_out.unit_cost, source_type="transfer", transfer_group_id=group,
            reason=reason,
        )
        audit(
            db, request, auth, "stock.transferred", "stock_transfer", str(group),
            {"sku": product.sku, "quantity": cd.quantity_text(qty)},
        )
    return leg_out, leg_in


def reconcile_count(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, product_id: UUID, location_id: UUID,
    counted_quantity: str, unit_cost: str | None, reason: str | None,
) -> dict[str, Any]:
    counted = cd.parse_count(counted_quantity)
    cost = None if unit_cost in (None, "") else cd.parse_rate(unit_cost, "unit_cost")
    product, location, warehouse = _stock_target(db, product_id, location_id)
    movement: StockMovement | None = None
    with unit_of_work(db):
        balance = _balance_row(db, product.id, location.id)  # locked, so the book figure cannot change under us
        book = balance.quantity
        delta = cd.reconciliation_delta(book, counted)
        if delta != 0:
            movement = move_stock(
                db, owner_id=auth.owner.id, product_id=product.id, location_id=location.id,
                allow_negative=warehouse.allow_negative_stock,
                movement_type=cd.MovementType.ADJUSTMENT_IN if delta > 0 else cd.MovementType.ADJUSTMENT_OUT,
                quantity_delta=delta, unit_cost=cost, source_type="stock_count", reason=reason or "Physical count",
                default_inbound_cost=delta > 0,
            )
        audit(
            db, request, auth, "stock.reconciled", "stock_balance", f"{product.sku}@{location.code}",
            {"book": cd.quantity_text(book), "counted": cd.quantity_text(counted), "delta": cd.quantity_text(delta)},
        )
    return {
        "book_quantity": cd.quantity_text(book),
        "counted_quantity": cd.quantity_text(counted),
        "delta": cd.quantity_text(delta),
        "movement_id": str(movement.id) if movement else None,
    }


# ---------------------------------------------------------------- reporting (read-only)


def stock_balances(
    db: DBSession, *, warehouse_id: UUID | None = None, product_id: UUID | None = None, non_zero_only: bool = False
) -> list[dict[str, Any]]:
    stmt = (
        select(StockBalance, Product, StockLocation, Warehouse)
        .join(Product, Product.id == StockBalance.product_id)
        .join(StockLocation, StockLocation.id == StockBalance.location_id)
        .join(Warehouse, Warehouse.id == StockLocation.warehouse_id)
        .order_by(Warehouse.code, Product.sku)
    )
    if warehouse_id is not None:
        stmt = stmt.where(Warehouse.id == warehouse_id)
    if product_id is not None:
        stmt = stmt.where(Product.id == product_id)
    if non_zero_only:
        stmt = stmt.where(StockBalance.quantity != 0)
    rows = []
    for balance, product, location, warehouse in db.execute(stmt).all():
        value = cd.q2(balance.quantity * balance.average_cost)
        rows.append(
            {
                "product_id": product.id,
                "sku": product.sku,
                "product_name": product.name,
                "unit_of_measure": product.unit_of_measure,
                "warehouse_id": warehouse.id,
                "warehouse_code": warehouse.code,
                "location_id": location.id,
                "location_code": location.code,
                "quantity": balance.quantity,
                "average_cost": balance.average_cost,
                "value": value,
            }
        )
    return rows


def valuation(db: DBSession, *, warehouse_id: UUID | None = None) -> dict[str, Any]:
    rows = stock_balances(db, warehouse_id=warehouse_id, non_zero_only=True)
    total = sum((row["value"] for row in rows), Decimal("0"))
    return {"rows": rows, "total_value": cd.q2(total)}


def list_movements(
    db: DBSession,
    *,
    product_id: UUID | None = None,
    location_id: UUID | None = None,
    movement_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[StockMovement, Product]], int]:
    limit = max(1, min(limit, catalog.MAX_PAGE))
    conditions = []
    if product_id is not None:
        conditions.append(StockMovement.product_id == product_id)
    if location_id is not None:
        conditions.append(StockMovement.location_id == location_id)
    if movement_type is not None:
        conditions.append(StockMovement.movement_type == movement_type)
    total = db.scalar(select(func.count()).select_from(StockMovement).where(*conditions)) or 0
    stmt = (
        select(StockMovement, Product)
        .join(Product, Product.id == StockMovement.product_id)
        .where(*conditions)
        .order_by(StockMovement.seq.desc())
        .limit(limit)
        .offset(max(offset, 0))
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()], int(total)


def integrity_check(db: DBSession) -> list[dict[str, str]]:
    """Balances must equal the sum of their movements. Any difference is reported, never silently fixed."""
    sums = (
        select(
            StockMovement.product_id,
            StockMovement.location_id,
            func.sum(StockMovement.quantity_delta).label("total"),
        )
        .group_by(StockMovement.product_id, StockMovement.location_id)
        .subquery()
    )
    stmt = (
        select(StockBalance, sums.c.total)
        .outerjoin(sums, (sums.c.product_id == StockBalance.product_id) & (sums.c.location_id == StockBalance.location_id))
    )
    problems: list[dict[str, str]] = []
    for balance, total in db.execute(stmt).all():
        expected = Decimal(total or 0)
        if balance.quantity != expected:
            problems.append(
                {
                    "product_id": str(balance.product_id),
                    "location_id": str(balance.location_id),
                    "balance": cd.quantity_text(balance.quantity),
                    "movements_total": cd.quantity_text(expected),
                }
            )
    return problems
