import json
from decimal import Decimal

import pytest

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import (
    CartLine,
    installment_allowed,
    max_installments,
    payment_options,
    pix_price,
)
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import get_clock
from emporio_agent.config import get_client_config

PAYMENTS = get_client_config().payments
BANDS = PAYMENTS.installment_bands


@pytest.fixture(scope="module")
def tools(tmp_path_factory, policy_retriever):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('db')}/store.db")
    load_store_data(engine)
    return {
        t.name: t
        for t in build_tools(
            SqlStoreRepository(engine), get_client_config(), get_clock(), policy_retriever
        )
    }


def call(tool, args):
    return json.loads(tool.invoke(args))


# --- installments (manual §3.1, P3) --------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        ("240", 3),  # AGENTS.md §14: 4x would be R$ 60 < R$ 80
        ("480", 6),  # 6x = R$ 80; 7x would be R$ 68,57 < R$ 100
        ("1200", 12),  # 12x = R$ 100
        ("99.99", 1),  # 2x would be < R$ 50; 1x is always allowed
        ("100", 2),
        ("150", 3),
        ("319.99", 3),
        ("320", 4),
        ("699.99", 6),
        ("700", 7),
        ("1199.99", 11),
        ("50000", 12),
    ],
)
def test_max_installments_band_boundaries(total, expected):
    assert max_installments(Decimal(total), BANDS) == expected


def test_one_installment_always_allowed():
    assert installment_allowed(Decimal("10"), 1, BANDS)
    assert not installment_allowed(Decimal("10"), 2, BANDS)
    assert not installment_allowed(Decimal("100000"), 13, BANDS)  # beyond the last band


# --- PIX and combined payment (manual §3, §3.1, §6.2) --------------------------------------


def test_pix_discount_only_on_non_promotional_prices():
    assert pix_price(Decimal("2199"), promotional=False, payments=PAYMENTS) == Decimal("2089.05")
    assert pix_price(Decimal("1967.18"), promotional=True, payments=PAYMENTS) == Decimal("1967.18")


def test_payment_options_mixed_cart():
    lines = [
        CartLine(Decimal("1967.18"), 1, promotional=True),
        CartLine(Decimal("199.90"), 2, promotional=False),
    ]
    options = payment_options(lines, PAYMENTS)
    assert options.total == Decimal("2366.98")
    assert options.pix_total == Decimal("1967.18") + Decimal("379.81")  # 399.80 − 5%
    assert options.max_installments == 12
    assert options.installments[0] == (1, Decimal("2366.98"))
    assert options.installments[-1] == (12, Decimal("197.25"))


@pytest.mark.parametrize(("total", "allowed"), [("2000", False), ("2000.01", True)])
def test_combined_payment_strictly_above_threshold(total, allowed):
    options = payment_options([CartLine(Decimal(total), 1, promotional=False)], PAYMENTS)
    assert options.combined_payment_allowed is allowed


# --- tool ----------------------------------------------------------------------------------


def test_payment_tool_single_product(tools):
    result = call(tools["calculate_payment_options"], {"items": [{"product_id": 95}]})
    assert result["total_text"] == "R$ 2.199,00"
    assert result["pix_total_text"] == "R$ 2.089,05"
    assert result["max_installments"] == 12
    assert result["installments"][-1] == "12x de R$ 183,25"
    assert result["boleto_clearing_business_days"] == 3
    assert result["combined_payment_allowed"] is True


def test_payment_tool_promotional_item_has_no_pix_discount(tools):
    result = call(tools["calculate_payment_options"], {"items": [{"product_id": 90}]})
    assert result["items"][0]["pix_discount"] is False
    assert result["pix_total"] == result["total"] == 1967.18


def test_payment_tool_merges_duplicates_and_reports_unsellable(tools):
    items = [
        {"product_id": 116},
        {"product_id": 116},
        {"product_id": 96},  # out of stock
        {"product_id": 9},  # unknown
    ]
    result = call(tools["calculate_payment_options"], {"items": items})
    assert result["items"][0]["quantity"] == 2
    assert {r["id"]: r["reason"] for r in result["not_for_sale"]} == {
        96: "esgotado",
        9: "product_not_found",
    }


def test_payment_tool_quantity_above_stock(tools):
    result = call(
        tools["calculate_payment_options"], {"items": [{"product_id": 93, "quantity": 5}]}
    )
    assert result["error"] == "nothing_to_sell"
    assert result["not_for_sale"][0]["available_quantity"] == 2


def test_product_details_payment_summary(tools):
    details = call(tools["get_product_details"], {"product_id": 95})
    assert details["payment"] == {
        "pix_price": 2089.05,
        "pix_price_text": "R$ 2.089,05",
        "max_installments": 12,
        "installment": 183.25,
        "installment_text": "R$ 183,25",
    }
    assert "payment" not in call(tools["get_product_details"], {"product_id": 96})  # esgotado
