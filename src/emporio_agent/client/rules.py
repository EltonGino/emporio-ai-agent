"""Business rules as pure functions: availability, prices and money formatting.

Tools call these so the LLM never decides availability or computes/formats money itself.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def availability(status: str, stock: int) -> str:
    """D1: status wins over stock. Only active items with stock can be sold."""
    if status == "discontinued":
        return "descontinuado"
    if status == "coming_soon":
        return "em_breve"
    return "disponivel" if stock > 0 else "esgotado"


def apply_discount(price: Decimal, percent: int) -> Decimal:
    return (price * (100 - percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def format_brl(value: Decimal) -> str:
    """Decimal → pt-BR money string, e.g. Decimal("1299") → "R$ 1.299,00"."""
    text = f"{value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"
    return "R$ " + text.replace(",", "_").replace(".", ",").replace("_", ".")
