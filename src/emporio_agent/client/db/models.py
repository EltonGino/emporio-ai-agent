"""SQLAlchemy models for the store data. Tables and columns mirror `data/raw/*.csv`."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PRODUCT_STATUSES = ("active", "discontinued", "coming_soon")
ORDER_STATUSES = ("pending", "confirmed", "shipped", "delivered", "cancelled")


def _one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{column}")


class Base(DeclarativeBase):
    type_annotation_map = {Decimal: Numeric(10, 2), dict[str, Any]: JSON}


class Category(Base):
    __tablename__ = "categories"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str]

    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (_one_of("status", PRODUCT_STATUSES),)

    product_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.category_id"))
    price_brl: Mapped[Decimal]
    description: Mapped[str]
    # False when the description contradicts the name/specs (data finding D2): never shown.
    description_reliable: Mapped[bool]
    stock_quantity: Mapped[int]
    status: Mapped[str] = mapped_column(String(20))
    specs: Mapped[dict[str, Any]]  # category-dependent keys (D10)
    created_at: Mapped[date]

    category: Mapped[Category] = relationship(back_populates="products")
    promotions: Mapped[list[Promotion]] = relationship(back_populates="product")


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    phone: Mapped[str]
    email: Mapped[str] = mapped_column(index=True)  # not unique: customers 11 and 43 share one (D8)
    city: Mapped[str]

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (_one_of("status", ORDER_STATUSES),)

    order_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"))
    order_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(20))
    total_brl: Mapped[Decimal]  # amount paid; can differ from the sum of items (D4)
    payment_method: Mapped[str]  # e.g. pix, debit, boleto, credit_6x
    tracking_code: Mapped[str | None]
    estimated_delivery: Mapped[date | None]  # also the proxy for the receipt date (D6)
    notes: Mapped[str | None]

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)  # surrogate key; the CSV has none
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.order_id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.product_id"))
    quantity: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Promotion(Base):
    __tablename__ = "promotions"

    promotion_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.product_id"), index=True)
    discount_percent: Mapped[int]
    description: Mapped[str]
    is_active: Mapped[bool]  # promotions have no dates; this flag is the truth (D3)

    product: Mapped[Product] = relationship(back_populates="promotions")


HANDOFF_REASONS = (
    "complaint",
    "delayed_order",
    "return_request",
    "manager_question",
    "unavailable_item",
    "other",
)
# Reasons about a specific order: they need an order verified (number + email) in the session.
ORDER_HANDOFF_REASONS = frozenset({"delayed_order", "return_request"})


class Handoff(Base):
    """Runtime table: requests passed to the human team. No foreign keys to store tables, so
    rebuilding the catalog never touches it."""

    __tablename__ = "handoffs"
    __table_args__ = (_one_of("reason", HANDOFF_REASONS),)

    id: Mapped[int] = mapped_column(primary_key=True)
    protocol: Mapped[str] = mapped_column(String(20), unique=True)  # EMP-YYYYMMDD-NNNN
    created_at: Mapped[datetime]  # store time (naive)
    session_id: Mapped[str | None] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(20))
    order_id: Mapped[int | None]  # plain number, no FK (runtime table)
    summary: Mapped[str]
    contact: Mapped[str | None]  # only if the customer offered it
    status: Mapped[str] = mapped_column(String(20), default="open")


# Rebuilt from data/raw by the loader. Runtime tables (handoffs, sessions) are kept.
STORE_MODELS: tuple[type[Base], ...] = (Category, Product, Customer, Order, OrderItem, Promotion)
