import json
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from emporio_agent.client import matching
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data
from emporio_agent.client.db.models import Promotion
from emporio_agent.client.repository import (
    ProductFilters,
    SqlStoreRepository,
    UnknownCategoryError,
)
from emporio_agent.client.rules import apply_discount, availability, format_brl
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import get_clock
from emporio_agent.config import get_client_config


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('db')}/store.db")
    load_store_data(engine)
    return engine


@pytest.fixture(scope="module")
def repo(engine):
    return SqlStoreRepository(engine)


@pytest.fixture(scope="module")
def tools(repo, policy_retriever):
    return {
        t.name: t for t in build_tools(repo, get_client_config(), get_clock(), policy_retriever)
    }


def call(tool, args=None):
    return json.loads(tool.invoke(args or {}))


def ids(search):
    return [p.id for p in search.products]


# --- rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "stock", "label"),
    [
        ("active", 5, "disponivel"),
        ("active", 0, "esgotado"),
        ("discontinued", 13, "descontinuado"),  # status wins over stock (D1)
        ("discontinued", 0, "descontinuado"),
        ("coming_soon", 2, "em_breve"),
    ],
)
def test_availability_status_wins_over_stock(status, stock, label):
    assert availability(status, stock) == label


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("1299", "R$ 1.299,00"),
        ("2089.05", "R$ 2.089,05"),
        ("0.5", "R$ 0,50"),
        ("12000", "R$ 12.000,00"),
        ("1234567.891", "R$ 1.234.567,89"),
    ],
)
def test_format_brl(value, text):
    assert format_brl(Decimal(value)) == text


def test_apply_discount_rounds_to_cents():
    assert apply_discount(Decimal("2399"), 18) == Decimal("1967.18")
    assert apply_discount(Decimal("359"), 10) == Decimal("323.10")


# --- matching ----------------------------------------------------------------------------


def test_terms_ignore_accents_case_plurals_and_hyphens():
    assert matching.query_terms("Violões de 7 Cordas") == ["violao", "7", "corda"]
    assert matching.document_terms("Takamine GD-20") >= {"takamine", "gd20", "gd", "20"}


@pytest.mark.parametrize(
    ("term", "expected"),
    [("takamini", True), ("yamha", True), ("kala", False), ("gd30", False)],
)
def test_fuzzy_term_matching(term, expected):
    document = matching.document_terms("Takamine GD20 Kalani Yamaha")
    assert matching.term_matches(term, document) is expected


# --- repository: availability and promotions ---------------------------------------------


@pytest.mark.parametrize(
    ("product_id", "label"),
    [(96, "esgotado"), (113, "descontinuado"), (136, "descontinuado"), (130, "em_breve")],
)
def test_product_availability_from_data(repo, product_id, label):
    assert repo.get_product(product_id).availability == label


def test_unreliable_description_is_hidden(repo):
    assert repo.get_product(135).description is None  # D2
    assert "Takamine" in repo.get_product(95).description


def test_only_active_promotions_apply(repo):
    assert repo.active_promotion(90).percent == 18
    assert repo.active_promotion(102) is None  # Black Friday, inactive (D3)
    assert repo.get_product(95).final_price == Decimal("2199")


def test_highest_active_promotion_wins(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    load_store_data(engine)
    with Session(engine) as session, session.begin():  # product 96 has promotions 8 (18%), 22 (40%)
        session.execute(
            update(Promotion).where(Promotion.promotion_id.in_([8, 22])).values(is_active=True)
        )
    assert SqlStoreRepository(engine).active_promotion(96).percent == 40


def test_list_active_promotions(repo):
    promos = {p.id: p.final_price for p in repo.list_active_promotions()}
    assert promos == {
        121: Decimal("439.20"),
        90: Decimal("1967.18"),
        127: Decimal("323.10"),
        94: Decimal("5519.08"),
    }


# --- repository: search -------------------------------------------------------------------


@pytest.mark.parametrize("query", ["takamine gd20", "Takamine GD-20", "takamini gd 20"])
def test_search_by_model_tolerates_typos_and_format(repo, query):
    assert ids(repo.find_products(ProductFilters(query=query))) == [95]


def test_search_seven_string_guitars(repo):
    found = repo.find_products(ProductFilters(query="violao 7 cordas"))
    assert set(ids(found)) == {109, 110, 111, 112}
    assert [p.id for p in found.unavailable] == [113]  # discontinued, reported apart


def test_search_out_of_stock_model_returns_alternatives(repo):
    found = repo.find_products(ProductFilters(query="Giannini GF-3D"))
    assert [p.id for p in found.unavailable] == [96]
    assert found.products and all("Giannini" in p.name for p in found.products)


def test_category_and_price_filter_sorted_by_price(repo):
    found = repo.find_products(ProductFilters(category="violões", max_price=Decimal("1000")))
    prices = [p.final_price for p in found.products]
    assert prices == sorted(prices) and max(prices) <= 1000
    assert all(p.availability == "disponivel" for p in found.products)
    assert 96 not in ids(found) and not found.unavailable


def test_price_filter_uses_promotional_price(repo):
    found = repo.find_products(
        ProductFilters(category="ukulele", min_price=Decimal("400"), max_price=Decimal("450"))
    )
    assert 121 in ids(found)  # list price 549, 20% off → 439.20


def test_include_unavailable(repo):
    found = repo.find_products(ProductFilters(query="kala barítono", only_available=False))
    assert set(ids(found)) == {128, 130}


def test_empty_category_returns_nothing(repo):
    assert ids(repo.find_products(ProductFilters(category="sopro"))) == []  # D9


def test_unknown_category(repo):
    with pytest.raises(UnknownCategoryError) as exc:
        repo.find_products(ProductFilters(category="amplificadores"))
    assert "Violões" in exc.value.known


def test_not_sold_model_finds_nothing(repo):
    assert ids(repo.find_products(ProductFilters(query="fender jazz bass"))) == []


# --- tools ---------------------------------------------------------------------------------


def test_search_products_output(tools):
    result = call(tools["search_products"], {"query": "crafter ht-100"})
    product = result["products"][0]
    assert product["price_text"] == "R$ 2.399,00"
    assert product["promo"] == {
        "name": "Desconto Exclusivo",
        "percent": 18,
        "final_price": 1967.18,
        "final_price_text": "R$ 1.967,18",
    }
    assert set(product["specs"]) == {"strings", "electronics", "top"}


def test_search_products_limit_and_total(tools):
    result = call(tools["search_products"], {"category": "violões"})
    assert len(result["products"]) == 5  # default limit (token budget)
    assert result["total_matches"] > 5


def test_search_products_unknown_category(tools):
    result = call(tools["search_products"], {"category": "pedais"})
    assert result["error"] == "unknown_category"


def test_product_details(tools):
    details = call(tools["get_product_details"], {"product_id": 95})
    assert details["specs"]["back_sides"] == "Sapele"
    assert "description" in details
    assert "description" not in call(tools["get_product_details"], {"product_id": 135})
    assert call(tools["get_product_details"], {"product_id": 9}) == {"error": "product_not_found"}


def test_list_active_promotions_tool(tools):
    promos = call(tools["list_active_promotions"])["promotions"]
    assert [p["promo"]["percent"] for p in promos] == [20, 18, 10, 8]


# --- partial matches and accessory queries --------------------------------------------------


def test_partial_match_reports_missing_terms(repo):
    found = repo.find_products(ProductFilters(query="ukulele preto"))
    assert found.partial_match and found.unmatched_terms == ["preto"]
    assert {p.category for p in found.products} == {"Ukuleles"}  # category word filters
    assert not found.unavailable  # only exact matches are reported as unavailable


def test_full_match_is_not_partial(repo):
    found = repo.find_products(ProductFilters(query="yamaha preto"))
    assert ids(found) == [104] and not found.partial_match


def test_exact_item_out_of_stock_is_partial_with_unavailable(repo):
    found = repo.find_products(ProductFilters(query="Giannini GF-3D"))
    assert found.unmatched_terms == ["gf3d"]


def test_search_tool_partial_fields(tools):
    result = call(tools["search_products"], {"query": "guitarra fender preta"})
    assert result["partial_match"] is True
    assert result["unmatched_terms"] == ["preta"]
    assert "partial_match" not in call(tools["search_products"], {"query": "takamine gd20"})


@pytest.mark.parametrize(
    "args",
    [
        {"query": "cordas"},
        {"query": "pedal de distorção"},
        {"query": "violão 7 cordas"},  # flagged, but results still returned
        {"category": "amplificadores"},
    ],
)
def test_accessory_queries_are_flagged(tools, args):
    assert call(tools["search_products"], args)["possible_accessory_query"] is True


def test_accessory_flag_does_not_block_results(tools):
    result = call(tools["search_products"], {"query": "violão 7 cordas"})
    assert {p["id"] for p in result["products"]} == {109, 110, 111, 112}


def test_instrument_query_is_not_flagged(tools):
    assert "possible_accessory_query" not in call(tools["search_products"], {"query": "ukulele"})
