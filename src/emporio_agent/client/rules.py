"""Business rules as pure functions: availability, prices, payments and money formatting.

Tools call these so the LLM never decides availability or computes/formats money itself.
Payment rules come from `client.yaml` (manual §3, §3.1, §6.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from emporio_agent.config import InstallmentBand, PaymentsConfig, ReturnReason, ReturnsConfig

CENT = Decimal("0.01")


def _dec(value: float | int | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def availability(status: str, stock: int) -> str:
    """D1: status wins over stock. Only active items with stock can be sold."""
    if status == "discontinued":
        return "descontinuado"
    if status == "coming_soon":
        return "em_breve"
    return "disponivel" if stock > 0 else "esgotado"


def apply_discount(price: Decimal, percent: float | int | Decimal) -> Decimal:
    return to_cents(price * (100 - _dec(percent)) / 100)


def format_brl(value: Decimal) -> str:
    """Decimal → pt-BR money string, e.g. Decimal("1299") → "R$ 1.299,00"."""
    text = f"{to_cents(value):,.2f}"
    return "R$ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


# --- payments --------------------------------------------------------------------------------


def installment_allowed(total: Decimal, n: int, bands: Sequence[InstallmentBand]) -> bool:
    """1x is always allowed; n installments need total / n ≥ the band's minimum (P3)."""
    if n == 1:
        return True
    band = next((b for b in bands if b.min_installments <= n <= b.max_installments), None)
    return band is not None and total / n >= _dec(band.min_installment)


def max_installments(total: Decimal, bands: Sequence[InstallmentBand]) -> int:
    """Highest allowed n. Band minimums rise as installments shrink, so 1..max are all allowed."""
    return max(
        n for n in range(1, bands[-1].max_installments + 1) if installment_allowed(total, n, bands)
    )


def pix_price(price: Decimal, promotional: bool, payments: PaymentsConfig) -> Decimal:
    """§6.2: the PIX discount doesn't apply on top of a promotional price."""
    if promotional and not payments.pix_applies_to_promotional:
        return price
    return apply_discount(price, payments.pix_discount_percent)


@dataclass(frozen=True)
class CartLine:
    unit_price: Decimal  # final price (promotion already applied)
    quantity: int
    promotional: bool

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class PaymentOptions:
    total: Decimal  # credit card, debit or boleto (no discount)
    pix_total: Decimal
    max_installments: int
    installments: list[tuple[int, Decimal]]  # (n, value of each installment), interest-free
    combined_payment_allowed: bool


def payment_options(lines: Sequence[CartLine], payments: PaymentsConfig) -> PaymentOptions:
    total = sum((line.total for line in lines), Decimal(0))
    pix_total = sum(
        (pix_price(line.total, line.promotional, payments) for line in lines), Decimal(0)
    )
    bands = payments.installment_bands
    max_n = max_installments(total, bands)
    return PaymentOptions(
        total=total,
        pix_total=pix_total,
        max_installments=max_n,
        installments=[(n, to_cents(total / n)) for n in range(1, max_n + 1)],
        # "acima de R$ 2.000,00" → strictly above
        combined_payment_allowed=total > _dec(payments.combined_payment_min_total),
    )


# --- returns (manual §4, §8.1) -----------------------------------------------------------------


@dataclass(frozen=True)
class ReturnDecision:
    eligible: bool
    rule_applied: str
    deadline: date | None = None
    days_elapsed: int | None = None
    receipt_date: date | None = None
    legal_warranty_until: date | None = None  # defects only (90 days from receipt)


def return_eligibility(
    reason: ReturnReason,
    status: str,
    order_date: date,
    estimated_delivery: date | None,
    today: date,
    returns: ReturnsConfig,
) -> ReturnDecision:
    """Deterministic return windows. The receipt date is estimated by the delivery ETA (D6)."""
    if status == "cancelled":
        return ReturnDecision(False, "order_cancelled")
    if status != "delivered":
        return ReturnDecision(False, "order_not_delivered")
    if estimated_delivery is None:
        return ReturnDecision(False, "receipt_date_unknown")

    receipt = min(estimated_delivery, today)  # a delivered order can't arrive in the future
    if reason == "defeito":
        deadline = order_date + timedelta(days=returns.defect_days_after_purchase)
        warranty = receipt + timedelta(days=returns.legal_warranty_days_after_receipt)
        rule = "defect_exchange_window" if today <= deadline else "defect_after_exchange_window"
        return ReturnDecision(
            today <= deadline, rule, deadline, (today - order_date).days, receipt, warranty
        )

    days = (
        returns.regret_days_after_receipt
        if reason == "arrependimento"
        else returns.preference_days_after_receipt
    )
    deadline = receipt + timedelta(days=days)
    rule = "regret_window" if reason == "arrependimento" else "preference_window"
    return ReturnDecision(today <= deadline, rule, deadline, (today - receipt).days, receipt)
