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


MIN_PREFIX_WORDS = 2  # brand + at least one more word: "Yamaha" alone names no product
CODE = re.compile(r"\b(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]{3,}\b")  # "gd20", "sgd195e"


def name_patterns(name: str, others: list[str]) -> list[str]:
    """What identifies a product in the answer: its full name, its alphanumeric model codes,
    and the shortest leading part of its name (2+ words) no other candidate shares. Owner's
    test: "Tagima Dallas Tuner" (catalog: "Tagima Dallas Tuner Aço Natural") got no card."""
    words = name.split()
    patterns = [name, *CODE.findall(name)]
    for k in range(MIN_PREFIX_WORDS, len(words)):
        prefix = " ".join(words[:k])
        if not any(f"{o} ".startswith(f"{prefix} ") for o in others):
            patterns.append(prefix)
            break
    return patterns


def mention(text: str, patterns: list[str]) -> int | None:
    """Where the first pattern occurs in the normalized text (as whole words), or None."""
    found = [i for p in patterns if (i := text.find(f" {p} ")) >= 0]
    return min(found) if found else None


def product_cards(answer: str, results: list[ToolMessage]) -> list[dict[str, Any]]:
    """A card for each product of this turn's catalog results that the validated answer
    mentions (full name, model code or unique leading words), in order of mention.
    No database lookups, inferred prices, descriptions, specs or inventory counts leave here.
    Brand-only or ambiguous wording ("o Yamaha", "a bateria acústica") gets no card.
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
    names = {product_id: normalized(p["name"]) for product_id, p in candidates.items()}
    for product_id, product in candidates.items():
        others = [n for other, n in names.items() if other != product_id]
        position = mention(text, name_patterns(names[product_id], others))
        if position is None:
            continue
        card = {key: value for key, value in product.items() if key in PUBLIC_FIELDS}
        promo = product.get("promo")
        if promo:
            card["promo"] = {key: promo[key] for key in ("percent", "final_price_text")}
        elif product.get("availability") == "disponivel":
            pix = product.get("pix_price_text") or product.get("payment", {}).get("pix_price_text")
            if pix:
                card["pix_price_text"] = pix
        cards.append((position, card))
    return [card for _, card in sorted(cards, key=lambda pc: pc[0])]
