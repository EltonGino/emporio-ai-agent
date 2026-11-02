"""Public product cards, projected exclusively from this turn's successful catalog tools."""

import json
import re
from typing import Any

from langchain_core.messages import ToolMessage
from unidecode import unidecode

CATALOG_TOOLS = {"search_products", "get_product_details", "list_active_promotions"}
PUBLIC_FIELDS = {"id", "name", "price_text", "availability"}


def normalized(text: str) -> str:
    text = re.sub(r"(?<=[a-z])[- ](?=\d)", "", unidecode(text).lower())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def product_cards(answer: str, results: list[ToolMessage]) -> list[dict[str, Any]]:
    """Full name or distinctive alphanumeric model code must occur in the validated answer.
    No database lookups, inferred prices, descriptions, specs or inventory counts leave here.
    Conservative matching intentionally omits cards for ambiguous brand-only mentions.
    """
    candidates = {}
    for result in results:
        if result.name not in CATALOG_TOOLS or result.status != "success":
            continue
        try:
            data = json.loads(result.text)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or "error" in data:
            continue
        products = (
            [data]
            if result.name == "get_product_details"
            else [
                p
                for key in ("products", "alternatives", "unavailable", "promotions")
                for p in data.get(key, [])
            ]
        )
        for product in products:
            if isinstance(product, dict) and "id" in product and "name" in product:
                candidates[product["id"]] = product
    cards = []
    text = f" {normalized(answer)} "
    for product in candidates.values():
        name = normalized(product["name"])
        codes = re.findall(r"\b(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]{3,}\b", name)
        if f" {name} " not in text and not any(f" {code} " in text for code in codes):
            continue
        card = {key: value for key, value in product.items() if key in PUBLIC_FIELDS}
        promo = product.get("promo")
        if promo:
            card["promo"] = {key: promo[key] for key in ("percent", "final_price_text")}
        elif product.get("availability") == "disponivel":
            pix = product.get("pix_price_text") or product.get("payment", {}).get("pix_price_text")
            if pix:
                card["pix_price_text"] = pix
        cards.append(card)
    return cards
