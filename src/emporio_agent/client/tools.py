"""LangChain tools: thin wrappers over the repository and rules.

Outputs are compact JSON with English keys; money comes as a float plus a pt-BR string
(`price` / `price_text`) so the LLM never computes or formats it. Docstrings are the tool
descriptions the LLM reads: short, saying when to use each tool (token budget).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from emporio_agent.client.repository import (
    ProductFilters,
    ProductInfo,
    StoreRepository,
    UnknownCategoryError,
)
from emporio_agent.client.rules import format_brl

# Specs shown in search results, in priority order (category-dependent keys, D10).
KEY_SPECS = ("strings", "electronics", "top", "keys", "type", "pieces", "shells", "body")
MAX_KEY_SPECS = 3


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def money(key: str, value: Decimal) -> dict[str, Any]:
    return {key: float(value), f"{key}_text": format_brl(value)}


def product_summary(product: ProductInfo, specs: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": product.id,
        "name": product.name,
        "category": product.category,
        **money("price", product.price),
        "availability": product.availability,
    }
    if product.promotion:
        data["promo"] = {
            "name": product.promotion.name,
            "percent": product.promotion.percent,
            **money("final_price", product.final_price),
        }
    if specs is None:
        keys = [k for k in KEY_SPECS if k in product.specs][:MAX_KEY_SPECS]
        specs = {k: product.specs[k] for k in keys}
    data["specs"] = specs
    return data


class SearchProductsArgs(BaseModel):
    query: str | None = Field(None, description="Name, brand, model or feature")
    category: str | None = Field(None, description="Category, e.g. 'violões', 'ukulele'")
    min_price: float | None = Field(None, ge=0, description="BRL, final price")
    max_price: float | None = Field(None, ge=0, description="BRL, final price")
    only_available: bool = Field(True, description="False to include unsellable items")
    limit: int = Field(5, ge=1, le=10)


class ProductIdArgs(BaseModel):
    product_id: int


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def build_tools(repo: StoreRepository) -> list[BaseTool]:
    @tool(args_schema=SearchProductsArgs)
    def search_products(
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        only_available: bool = True,
        limit: int = 5,
    ) -> str:
        """Search the instrument catalog: price, promotion, availability, alternatives.
        Use for any product question. availability: disponivel | esgotado (out of stock) |
        descontinuado | em_breve (not for sale yet). `unavailable` = matches that can't be
        sold now; then offer available alternatives."""
        filters = ProductFilters(
            query=query,
            category=category,
            min_price=_decimal(min_price),
            max_price=_decimal(max_price),
            only_available=only_available,
        )
        try:
            found = repo.find_products(filters)
        except UnknownCategoryError as exc:
            return to_json({"error": "unknown_category", "categories": exc.known})
        result: dict[str, Any] = {
            "total_matches": len(found.products),
            "products": [product_summary(p) for p in found.products[:limit]],
        }
        if found.unavailable:
            result["unavailable"] = [
                {"id": p.id, "name": p.name, "availability": p.availability}
                for p in found.unavailable
            ]
        return to_json(result)

    @tool(args_schema=ProductIdArgs)
    def get_product_details(product_id: int) -> str:
        """Full specs and description of one product (id from search_products)."""
        product = repo.get_product(product_id)
        if product is None:
            return to_json({"error": "product_not_found"})
        data = product_summary(product, specs=product.specs)
        if product.description:  # unreliable descriptions are never shown (D2)
            data["description"] = product.description
        return to_json(data)

    @tool
    def list_active_promotions() -> str:
        """Promotions active today, with original and final price. Any other promotion
        (e.g. a past Black Friday) is not active."""
        return to_json({"promotions": [product_summary(p) for p in repo.list_active_promotions()]})

    return [search_products, get_product_details, list_active_promotions]
