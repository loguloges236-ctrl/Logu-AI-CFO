"""Products (SKU master), categories, parties, warehouses, stock locations and accounting role mappings."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.commerce import domain as cd
from app.models import (
    Account,
    CommerceAccountMapping,
    Party,
    Product,
    ProductCategory,
    StockBalance,
    StockLocation,
    StockMovement,
    Warehouse,
)
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, not_found, rule, unit_of_work
from app.services.commerce_ledger import account_is_usable

MAX_PAGE = 200
PRODUCT_CHANGEABLE = frozenset(
    {
        "name",
        "description",
        "category_id",
        "unit_of_measure",
        "hsn_sac",
        "tax_rate",
        "purchase_price",
        "selling_price",
        "barcode",
        "is_stock_item",
        "is_active",
    }
)


def _price(value: str | None, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    return cd.parse_amount(value, field)


def _tax(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return cd.parse_percent(value, "tax_rate")


# ---------------------------------------------------------------- categories


def list_categories(db: DBSession) -> list[ProductCategory]:
    return list(db.scalars(select(ProductCategory).order_by(ProductCategory.name)))


def create_category(db: DBSession, request: Request, auth: AuthenticatedSession, name: str, description: str | None) -> ProductCategory:
    value = cd.validate_name(name, "Category name")
    if db.scalar(select(ProductCategory.id).where(func.lower(ProductCategory.name) == value.lower())) is not None:
        raise rule("duplicate_category", "A category with this name already exists")
    with unit_of_work(db):
        category = ProductCategory(id=uuid.uuid4(), name=value, description=(description or None))
        db.add(category)
        audit(db, request, auth, "category.created", "product_category", str(category.id), {"name": value})
    return category


# ---------------------------------------------------------------- products


def get_product(db: DBSession, product_id: UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise not_found("Product")
    return product


def require_active_product(db: DBSession, product_id: UUID, *, stock: bool = True) -> Product:
    """Inactive products cannot be newly transacted. Non-stock items skip inventory."""
    product = get_product(db, product_id)
    if not product.is_active:
        raise rule("inactive_product", f"{product.sku} is inactive and cannot be used on new documents")
    if stock and not product.is_stock_item:
        raise rule("not_stock_item", f"{product.sku} is a service item and holds no stock")
    return product


def list_products(
    db: DBSession, *, search: str | None = None, active_only: bool = False, limit: int = 50, offset: int = 0
) -> tuple[list[Product], int]:
    limit = max(1, min(limit, MAX_PAGE))
    conditions = []
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append((Product.name.ilike(pattern)) | (Product.sku.ilike(pattern)) | (Product.barcode == search.strip()))
    if active_only:
        conditions.append(Product.is_active.is_(True))
    total = db.scalar(select(func.count()).select_from(Product).where(*conditions)) or 0
    rows = list(db.scalars(select(Product).where(*conditions).order_by(Product.name).limit(limit).offset(max(offset, 0))))
    return rows, int(total)


def create_product(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    sku: str,
    name: str,
    description: str | None,
    category_id: UUID | None,
    unit_of_measure: str,
    hsn_sac: str | None,
    tax_rate: str | None,
    purchase_price: str | None,
    selling_price: str | None,
    barcode: str | None,
    is_stock_item: bool,
) -> Product:
    code = cd.validate_sku(sku)
    if db.scalar(select(Product.id).where(func.lower(Product.sku) == code.lower())) is not None:
        raise rule("duplicate_sku", "A product with this SKU already exists")
    barcode_value = cd.validate_barcode(barcode)
    if barcode_value and db.scalar(select(Product.id).where(Product.barcode == barcode_value)) is not None:
        raise rule("duplicate_barcode", "Another product already uses this barcode")
    if category_id is not None and db.get(ProductCategory, category_id) is None:
        raise rule("invalid_category", "The selected category does not exist")
    with unit_of_work(db):
        product = Product(
            id=uuid.uuid4(),
            sku=code,
            barcode=barcode_value,
            name=cd.validate_name(name),
            description=description or None,
            category_id=category_id,
            unit_of_measure=cd.validate_uom(unit_of_measure),
            hsn_sac=cd.validate_hsn_sac(hsn_sac),
            tax_rate=_tax(tax_rate),
            purchase_price=_price(purchase_price, "purchase_price"),
            selling_price=_price(selling_price, "selling_price"),
            is_stock_item=is_stock_item,
            is_active=True,
        )
        db.add(product)
        audit(db, request, auth, "product.created", "product", code, {"stock_item": is_stock_item})
    return product


def update_product(
    db: DBSession, request: Request, auth: AuthenticatedSession, product_id: UUID, changes: dict[str, Any]
) -> Product:
    """SKU is fixed after creation. The unit of measure is locked once stock has moved."""
    product = get_product(db, product_id)
    if "sku" in changes and changes["sku"] is not None and cd.validate_sku(changes["sku"]) != product.sku:
        raise rule("sku_immutable", "The SKU cannot be changed after the product is created")
    unknown = set(changes) - PRODUCT_CHANGEABLE - {"sku"}
    if unknown:
        raise rule("invalid_field", "Unknown product field: " + ", ".join(sorted(unknown)))
    has_history = db.scalar(select(StockMovement.id).where(StockMovement.product_id == product.id).limit(1)) is not None
    if has_history and changes.get("unit_of_measure") not in (None, product.unit_of_measure):
        raise rule("uom_locked", "The unit of measure is locked because this product has stock history")
    if has_history and changes.get("is_stock_item") not in (None, product.is_stock_item):
        raise rule("stock_type_locked", "Stock or service type is locked because this product has stock history")
    changes = {key: value for key, value in changes.items() if value is not None or key in ("category_id", "barcode", "hsn_sac", "tax_rate", "purchase_price", "selling_price", "description")}
    if changes.get("barcode") is not None:
        barcode_value = cd.validate_barcode(changes["barcode"])
        if barcode_value and db.scalar(
            select(Product.id).where(Product.barcode == barcode_value, Product.id != product.id)
        ) is not None:
            raise rule("duplicate_barcode", "Another product already uses this barcode")
        changes = {**changes, "barcode": barcode_value}
    if changes.get("category_id") and db.get(ProductCategory, changes["category_id"]) is None:
        raise rule("invalid_category", "The selected category does not exist")

    applied: list[str] = []
    with unit_of_work(db):
        for key, value in changes.items():
            if key in ("name", "unit_of_measure", "is_stock_item", "is_active") and value is None:
                continue
            if key in ("tax_rate", "purchase_price", "selling_price", "hsn_sac", "unit_of_measure", "name"):
                value = {
                    "tax_rate": _tax,
                    "purchase_price": lambda v: _price(v, "purchase_price"),
                    "selling_price": lambda v: _price(v, "selling_price"),
                    "hsn_sac": cd.validate_hsn_sac,
                    "unit_of_measure": cd.validate_uom,
                    "name": cd.validate_name,
                }[key](value)
            if getattr(product, key) != value:
                setattr(product, key, value)
                applied.append(key)
        if applied:
            audit(db, request, auth, "product.updated", "product", product.sku, {"fields": ",".join(sorted(applied))})
    return product


# ---------------------------------------------------------------- parties


def list_parties(db: DBSession, kind: str | None = None, active_only: bool = False) -> list[Party]:
    conditions = []
    if kind:
        conditions.append(Party.kind.in_([kind, "both"]))
    if active_only:
        conditions.append(Party.is_active.is_(True))
    return list(db.scalars(select(Party).where(*conditions).order_by(Party.name)))


def get_party(db: DBSession, party_id: UUID) -> Party:
    party = db.get(Party, party_id)
    if party is None:
        raise not_found("Party")
    return party


def require_party(db: DBSession, party_id: UUID, role: str) -> Party:
    party = get_party(db, party_id)
    if not party.is_active:
        raise rule("inactive_party", "This party is inactive")
    if not cd.party_allowed(party.kind, role):
        raise rule("wrong_party_type", f"This party cannot be used as a {role}")
    return party


def create_party(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    kind: str,
    name: str,
    tax_id: str | None,
    email: str | None,
    phone: str | None,
) -> Party:
    if kind not in cd.PARTY_KINDS:
        raise rule("invalid_party_kind", "Party kind must be supplier, customer or both")
    if tax_id and len(tax_id.strip()) > 20:
        raise rule("invalid_tax_id", "Tax ID must be at most 20 characters")
    with unit_of_work(db):
        party = Party(
            id=uuid.uuid4(),
            kind=kind,
            name=cd.validate_name(name),
            tax_id=(tax_id or "").strip() or None,
            email=(email or "").strip() or None,
            phone=(phone or "").strip() or None,
            is_active=True,
        )
        db.add(party)
        audit(db, request, auth, "party.created", "party", str(party.id), {"kind": kind})
    return party


def update_party(
    db: DBSession, request: Request, auth: AuthenticatedSession, party_id: UUID, changes: dict[str, Any]
) -> Party:
    party = get_party(db, party_id)
    allowed = {"name", "tax_id", "email", "phone", "is_active", "kind"}
    if set(changes) - allowed:
        raise rule("invalid_field", "Unknown party field")
    if "kind" in changes and changes["kind"] not in cd.PARTY_KINDS:
        raise rule("invalid_party_kind", "Party kind must be supplier, customer or both")
    with unit_of_work(db):
        for key, value in changes.items():
            if key == "name" and value is not None:
                value = cd.validate_name(value)
            setattr(party, key, value)
        audit(db, request, auth, "party.updated", "party", str(party.id), {"fields": ",".join(sorted(changes))})
    return party


# ---------------------------------------------------------------- warehouses and locations


def list_warehouses(db: DBSession) -> list[Warehouse]:
    return list(db.scalars(select(Warehouse).order_by(Warehouse.code)))


def get_warehouse(db: DBSession, warehouse_id: UUID) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise not_found("Warehouse")
    return warehouse


def create_warehouse(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, code: str, name: str, allow_negative_stock: bool
) -> Warehouse:
    value = cd.validate_code(code, "Warehouse code")
    if db.scalar(select(Warehouse.id).where(Warehouse.code == value)) is not None:
        raise rule("duplicate_code", "A warehouse with this code already exists")
    with unit_of_work(db):
        warehouse = Warehouse(
            id=uuid.uuid4(),
            code=value,
            name=cd.validate_name(name),
            allow_negative_stock=allow_negative_stock,
            is_active=True,
        )
        db.add(warehouse)
        audit(
            db,
            request,
            auth,
            "warehouse.created",
            "warehouse",
            value,
            {"allow_negative_stock": allow_negative_stock},
        )
    return warehouse


def update_warehouse(
    db: DBSession, request: Request, auth: AuthenticatedSession, warehouse_id: UUID, changes: dict[str, Any]
) -> Warehouse:
    """The code is fixed. Changing the negative-stock permission is audited because it changes risk."""
    warehouse = get_warehouse(db, warehouse_id)
    if set(changes) - {"name", "allow_negative_stock", "is_active"}:
        raise rule("invalid_field", "Unknown warehouse field")
    with unit_of_work(db):
        if "name" in changes and changes["name"] is not None:
            warehouse.name = cd.validate_name(changes["name"])
        if "allow_negative_stock" in changes and changes["allow_negative_stock"] is not None:
            warehouse.allow_negative_stock = bool(changes["allow_negative_stock"])
            audit(
                db,
                request,
                auth,
                "warehouse.negative_stock_changed",
                "warehouse",
                warehouse.code,
                {"allow_negative_stock": warehouse.allow_negative_stock},
            )
        if "is_active" in changes and changes["is_active"] is not None:
            warehouse.is_active = bool(changes["is_active"])
        audit(db, request, auth, "warehouse.updated", "warehouse", warehouse.code, {"fields": ",".join(sorted(changes))})
    return warehouse


def list_locations(db: DBSession, warehouse_id: UUID | None = None) -> list[StockLocation]:
    stmt = select(StockLocation).order_by(StockLocation.code)
    if warehouse_id is not None:
        stmt = stmt.where(StockLocation.warehouse_id == warehouse_id)
    return list(db.scalars(stmt))


def require_location(db: DBSession, location_id: UUID, warehouse_id: UUID | None = None) -> tuple[StockLocation, Warehouse]:
    """A location must exist, be active, belong to an active warehouse, and match the warehouse if one is given."""
    location = db.get(StockLocation, location_id)
    if location is None:
        raise rule("invalid_location", "The selected stock location does not exist")
    warehouse = get_warehouse(db, location.warehouse_id)
    if warehouse_id is not None and location.warehouse_id != warehouse_id:
        raise rule("invalid_location", "The location does not belong to the selected warehouse")
    if not location.is_active or not warehouse.is_active:
        raise rule("invalid_location", "The stock location or its warehouse is inactive")
    return location, warehouse


def create_location(
    db: DBSession, request: Request, auth: AuthenticatedSession, *, warehouse_id: UUID, code: str, name: str
) -> StockLocation:
    get_warehouse(db, warehouse_id)
    value = cd.validate_code(code, "Location code")
    if db.scalar(select(StockLocation.id).where(StockLocation.warehouse_id == warehouse_id, StockLocation.code == value)):
        raise rule("duplicate_code", "This location code already exists in the warehouse")
    with unit_of_work(db):
        location = StockLocation(id=uuid.uuid4(), warehouse_id=warehouse_id, code=value, name=cd.validate_name(name), is_active=True)
        db.add(location)
        audit(db, request, auth, "location.created", "stock_location", str(location.id), {"code": value})
    return location


def update_location(db: DBSession, request: Request, auth: AuthenticatedSession, location_id: UUID, changes: dict[str, Any]) -> StockLocation:
    location = db.get(StockLocation, location_id)
    if location is None:
        raise not_found("Stock location")
    if set(changes) - {"name", "is_active"}:
        raise rule("invalid_field", "Unknown location field")
    with unit_of_work(db):
        if changes.get("name") is not None:
            location.name = cd.validate_name(changes["name"])
        if changes.get("is_active") is not None:
            location.is_active = bool(changes["is_active"])
        audit(db, request, auth, "location.updated", "stock_location", str(location.id), {"fields": ",".join(sorted(changes))})
    return location


# ---------------------------------------------------------------- accounting roles


def list_mappings(db: DBSession) -> list[CommerceAccountMapping]:
    return list(db.scalars(select(CommerceAccountMapping).order_by(CommerceAccountMapping.role)))


def set_mapping(db: DBSession, request: Request, auth: AuthenticatedSession, role: str, account_id: UUID) -> CommerceAccountMapping:
    """Point a business role at an active detail account whose category fits the role."""
    if role not in cd.ACCOUNT_ROLE_CATEGORIES:
        raise rule("invalid_role", "Unknown accounting role")
    allowed_categories = cd.ACCOUNT_ROLE_CATEGORIES[role]
    account = db.get(Account, account_id)
    if account is None:
        raise rule("invalid_account", "The selected account does not exist")
    if account.category not in allowed_categories:
        raise rule("invalid_account", f"This role needs a {' or '.join(sorted(allowed_categories))} account")
    account_is_usable(db, account_id, account.category)
    with unit_of_work(db):
        mapping = db.get(CommerceAccountMapping, role)
        if mapping is None:
            mapping = CommerceAccountMapping(role=role, account_id=account_id, updated_by_owner_id=auth.owner.id)
            db.add(mapping)
        else:
            mapping.account_id = account_id
            mapping.updated_by_owner_id = auth.owner.id
        audit(db, request, auth, "accounting_role.set", "commerce_account_mapping", role, {"account_code": account.code})
    return mapping


def stock_on_hand_for_product(db: DBSession, product_id: UUID) -> Decimal:
    value = db.scalar(select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(StockBalance.product_id == product_id))
    return Decimal(value or 0)
