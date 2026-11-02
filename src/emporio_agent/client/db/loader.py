"""Load the raw CSVs into the store database, applying the cleaning rules (AGENTS.md §5.3).

Idempotent: the store tables are dropped and rebuilt from `data/raw` on every run.
Runtime tables (handoffs, sessions) are created if missing and never dropped.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from emporio_agent.client.db.models import (
    HANDOFF_REASONS,
    ORDER_STATUSES,
    PRODUCT_STATUSES,
    STORE_MODELS,
    Base,
    Category,
    Customer,
    Handoff,
    Order,
    OrderItem,
    Product,
    Promotion,
)
from emporio_agent.config import PROJECT_ROOT

RAW_DIR = PROJECT_ROOT / "data" / "raw"

# D2: these descriptions describe other instruments (another brand, 88 keys vs 61 in specs,
# 5 strings vs 4...). The whole synthetic batch 135–144 is distrusted; specs are the truth.
UNRELIABLE_DESCRIPTION_IDS = frozenset(range(135, 145))


def read_csv(path: Path) -> list[dict[str, str]]:
    """Rows as dicts with keys and values stripped (the files use CRLF line endings)."""
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {key.strip(): (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(f)
        ]


def _optional(value: str) -> str | None:
    return value or None


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"unknown {field} {value!r} (expected one of {allowed})")
    return value


def _flag(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError(f"expected 0/1, got {value!r}")
    return value == "1"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def parse_category(row: dict[str, str]) -> Category:
    return Category(
        category_id=int(row["category_id"]),
        name=row["name"],
        description=row["description"],
    )


def parse_product(row: dict[str, str]) -> Product:
    product_id = int(row["product_id"])
    return Product(
        product_id=product_id,
        name=row["name"],
        category_id=int(row["category_id"]),
        price_brl=Decimal(row["price_brl"]),
        description=row["description"],
        description_reliable=product_id not in UNRELIABLE_DESCRIPTION_IDS,
        stock_quantity=int(row["stock_quantity"]),
        status=_choice(row["status"], PRODUCT_STATUSES, "product status"),
        specs=json.loads(row["specs"]) if row["specs"] else {},
        created_at=date.fromisoformat(row["created_at"]),
    )


def parse_customer(row: dict[str, str]) -> Customer:
    return Customer(
        customer_id=int(row["customer_id"]),
        name=row["name"],
        phone=row["phone"],
        email=normalize_email(row["email"]),
        city=row["city"],
    )


def parse_order(row: dict[str, str]) -> Order:
    return Order(
        order_id=int(row["order_id"]),
        customer_id=int(row["customer_id"]),
        order_date=date.fromisoformat(row["order_date"]),
        status=_choice(row["status"], ORDER_STATUSES, "order status"),
        total_brl=Decimal(row["total_brl"]),
        payment_method=row["payment_method"],
        tracking_code=_optional(row["tracking_code"]),
        estimated_delivery=_optional_date(row["estimated_delivery"]),
        notes=_optional(row["notes"]),
    )


def parse_order_item(row: dict[str, str]) -> OrderItem:
    return OrderItem(
        order_id=int(row["order_id"]),
        product_id=int(row["product_id"]),
        quantity=int(row["quantity"]),
    )


def parse_promotion(row: dict[str, str]) -> Promotion:
    return Promotion(
        promotion_id=int(row["promotion_id"]),
        product_id=int(row["product_id"]),
        discount_percent=int(row["discount_percent"]),
        description=row["description"],
        is_active=_flag(row["is_active"]),
    )


# Parent tables first so foreign keys are satisfied.
CSV_PARSERS = {
    "categories": parse_category,
    "products": parse_product,
    "customers": parse_customer,
    "orders": parse_order,
    "order_items": parse_order_item,
    "promotions": parse_promotion,
}


def load_store_data(engine: Engine, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Drop and rebuild the store tables from the CSVs. Returns row counts per table."""
    records: dict[str, list[Base]] = {}
    for name, parse in CSV_PARSERS.items():
        rows = read_csv(raw_dir / f"{name}.csv")
        try:
            records[name] = [parse(row) for row in rows]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{name}.csv: {exc}") from exc

    Base.metadata.drop_all(engine, tables=[model.__table__ for model in STORE_MODELS])
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for objects in records.values():
            session.add_all(objects)
            session.flush()
    return {name: len(objects) for name, objects in records.items()}


def upgrade_handoffs_table(engine: Engine) -> bool:
    """SQLite databases created before a schema change of `handoffs` (a new reason in the
    CHECK, `summary` made nullable) can't be altered in place: rebuild the table, keeping its
    rows. (A real deployment would use migrations, e.g. Alembic.) True if it rebuilt it."""
    if engine.dialect.name != "sqlite":
        return False
    with engine.begin() as conn:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'handoffs'")
        ).scalar()
        current = all(f"'{reason}'" in sql for reason in HANDOFF_REASONS) if sql else True
        if sql is None or (current and "summary VARCHAR NOT NULL" not in sql):
            return False
        conn.execute(text("ALTER TABLE handoffs RENAME TO handoffs_old"))
        conn.execute(text("DROP INDEX IF EXISTS ix_handoffs_session_id"))
        Handoff.__table__.create(conn)
        old_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(handoffs_old)"))}
        columns = ", ".join(c.name for c in Handoff.__table__.columns if c.name in old_columns)
        conn.execute(text(f"INSERT INTO handoffs ({columns}) SELECT {columns} FROM handoffs_old"))
        conn.execute(text("DROP TABLE handoffs_old"))
    return True


def ensure_store_data(engine: Engine, raw_dir: Path = RAW_DIR) -> bool:
    """Build the store tables if they don't exist yet. Returns True if it built them."""
    if inspect(engine).has_table(Product.__tablename__):
        upgrade_handoffs_table(engine)
        Base.metadata.create_all(engine)  # runtime tables added by later versions
        return False
    load_store_data(engine, raw_dir)
    return True
