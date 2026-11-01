import shutil
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import (
    RAW_DIR,
    ensure_store_data,
    load_store_data,
    parse_order,
    parse_product,
    parse_promotion,
    read_csv,
)
from emporio_agent.client.db.models import Customer, Order, OrderItem, Product, Promotion

EXPECTED_COUNTS = {
    "categories": 9,
    "products": 65,
    "customers": 50,
    "orders": 20,
    "order_items": 22,
    "promotions": 25,
}


@pytest.fixture
def engine(tmp_path):
    return make_engine(f"sqlite:///{tmp_path}/store.db")


@pytest.fixture
def loaded(engine):
    load_store_data(engine)
    return engine


def test_load_counts(engine):
    assert load_store_data(engine) == EXPECTED_COUNTS


def test_load_is_idempotent(engine):
    load_store_data(engine)
    assert load_store_data(engine) == EXPECTED_COUNTS
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 65


def test_products_are_typed_and_cleaned(loaded):
    with Session(loaded) as session:
        c40 = session.get(Product, 81)
        assert c40.price_brl == Decimal("599.90")
        assert c40.specs["top"] == "Spruce"  # D10: specs parsed to dict
        assert c40.created_at == date(2021, 3, 15)
        assert c40.category.name == "Violões"


def test_unreliable_descriptions_flagged(loaded):
    """D2: products 135–144 have descriptions of other instruments."""
    with Session(loaded) as session:
        flagged = session.scalars(
            select(Product.product_id).where(Product.description_reliable.is_(False))
        ).all()
    assert sorted(flagged) == list(range(135, 145))


def test_only_flagged_promotions_are_active(loaded):
    """D3: no dates, the is_active flag is the truth."""
    with Session(loaded) as session:
        active = session.scalars(
            select(Promotion.product_id).where(Promotion.is_active.is_(True))
        ).all()
    assert sorted(active) == [90, 94, 121, 127]


def test_orders_optional_fields(loaded):
    with Session(loaded) as session:
        shipped = session.get(Order, 8)
        assert shipped.status == "shipped"
        assert shipped.tracking_code == "BRJL5544332BR"
        assert shipped.estimated_delivery == date(2026, 2, 28)
        assert shipped.notes is None
        confirmed = session.get(Order, 9)
        assert confirmed.tracking_code is None and confirmed.estimated_delivery is None
        assert session.get(Order, 17).notes.startswith("Pedido cancelado")


def test_order_total_is_kept_as_paid(loaded):
    """D4: order 3 paid 3450 although its items list at 2 x 1749."""
    with Session(loaded) as session:
        order = session.get(Order, 3)
        assert order.total_brl == Decimal("3450")
        assert [(i.product_id, i.quantity) for i in order.items] == [(130, 2)]


def test_emails_are_normalized_and_duplicates_kept(loaded):
    """D8: customers 11 and 43 share an email; both rows stay."""
    with Session(loaded) as session:
        emails = session.scalars(select(Customer.email)).all()
        assert all(e == e.strip().lower() for e in emails)
        shared = session.scalars(
            select(Customer.customer_id).where(Customer.email == "bruno.martins@jmail.com")
        ).all()
        assert sorted(shared) == [11, 43]


def test_foreign_keys_are_enforced(loaded):
    with Session(loaded) as session, pytest.raises(IntegrityError):
        session.add(OrderItem(order_id=1, product_id=9999, quantity=1))
        session.flush()


def test_read_csv_strips_whitespace_and_crlf():
    row = read_csv(RAW_DIR / "categories.csv")[-1]
    assert row == {
        "category_id": "9",
        "name": "Ukuleles",
        "description": "Ukuleles soprano, concert, tenor e barítono",
    }


def test_unknown_status_is_rejected():
    row = read_csv(RAW_DIR / "orders.csv")[0] | {"status": "lost"}
    with pytest.raises(ValueError, match="unknown order status"):
        parse_order(row)
    product = read_csv(RAW_DIR / "products.csv")[0] | {"status": "sold_out"}
    with pytest.raises(ValueError, match="unknown product status"):
        parse_product(product)


def test_invalid_active_flag_is_rejected():
    row = read_csv(RAW_DIR / "promotions.csv")[0] | {"is_active": "yes"}
    with pytest.raises(ValueError, match="0/1"):
        parse_promotion(row)


def test_bad_csv_reports_the_file(engine, tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(RAW_DIR, raw)
    orders = raw / "orders.csv"
    orders.write_text(orders.read_text(encoding="utf-8").replace(",shipped,", ",lost,"), "utf-8")
    with pytest.raises(ValueError, match=r"orders\.csv: unknown order status"):
        load_store_data(engine, raw)


def test_ensure_store_data_builds_once(engine):
    assert ensure_store_data(engine) is True
    assert ensure_store_data(engine) is False
