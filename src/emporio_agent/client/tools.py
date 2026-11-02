"""LangChain tools: thin wrappers over the repository and rules.

Outputs are compact JSON with English keys; money comes as a float plus a pt-BR string
(`price` / `price_text`) so the LLM never computes or formats it. Docstrings are the tool
descriptions the LLM reads: short, saying when to use each tool (token budget).
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from emporio_agent.client import identity, matching
from emporio_agent.client.repository import (
    OrderInfo,
    ProductFilters,
    ProductInfo,
    StoreRepository,
    UnknownCategoryError,
)
from emporio_agent.client.rules import (
    CartLine,
    format_brl,
    max_installments,
    payment_options,
    pix_price,
    return_eligibility,
    to_cents,
)
from emporio_agent.clock import Clock
from emporio_agent.config import ClientConfig, ReturnReason

ORDER_STATUS_TEXT = {
    "pending": "aguardando confirmação do pagamento",
    "confirmed": "confirmado, em preparação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
PAYMENT_METHOD_TEXT = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}
RETURN_NEXT_STEPS = {
    "eligible": "Confirm the conditions with the customer, then open a request with "
    "handoff_to_human (reason other) so the team arranges it.",
    "defect_after_exchange_window": "Exchange window over: guide the customer to the "
    "manufacturer's warranty (certificate with the product); the store can mediate on request "
    "(handoff_to_human). Mention the legal warranty if still valid.",
    "regret_window": "Window over. If the product has a manufacturing defect, check again with "
    "reason defeito.",
    "preference_window": "Window over. If the product has a manufacturing defect, check again "
    "with reason defeito.",
    "order_not_delivered": "Return windows start at receipt. Use get_order_status; for "
    "cancelling an undelivered order, offer handoff_to_human.",
    "order_cancelled": "The order was cancelled; there is nothing to return.",
    "receipt_date_unknown": "Offer handoff_to_human so the team checks the delivery date.",
}

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


class CartItem(BaseModel):
    product_id: int
    quantity: int = Field(1, ge=1, le=20)


class PaymentArgs(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=10)


class OrderLookupArgs(BaseModel):
    order_id: int = Field(ge=1)
    email: str = Field(max_length=254, description="Email used in the purchase")


class ReturnArgs(OrderLookupArgs):
    reason: ReturnReason


def br_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def payment_method_text(method: str) -> str:
    if match := re.fullmatch(r"credit_(\d+)x", method):
        return f"cartão de crédito em {match.group(1)}x"
    return PAYMENT_METHOD_TEXT.get(method, method)


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def build_tools(
    repo: StoreRepository,
    client: ClientConfig,
    clock: Clock,
    failed_lookups: identity.FailedLookups | None = None,
) -> list[BaseTool]:
    payments = client.payments
    failed_lookups = failed_lookups or identity.FailedLookups()

    def identified_order(order_id: int, email: str, config: RunnableConfig) -> OrderInfo | str:
        """The order, or the error JSON to return. Same error for unknown order / wrong email."""
        session = identity.session_id(config)
        if session is None:
            return identity.NO_SESSION
        if failed_lookups.locked(session):
            return identity.TOO_MANY_ATTEMPTS
        order = repo.get_order_for_customer(order_id, email)
        if order is None:
            failed_lookups.record_failure(session)
            return identity.ORDER_NOT_FOUND
        return order

    accessory_terms = matching.document_terms(" ".join(client.catalog.non_sold_accessories))

    def mentions_accessory(*texts: str | None) -> bool:
        terms = matching.query_terms(" ".join(t for t in texts if t))
        return any(matching.term_matches(t, accessory_terms) for t in terms)

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
        descontinuado | em_breve (not for sale yet). `unavailable` = exact matches that can't
        be sold now; then offer available alternatives. `partial_match` = the exact item isn't
        in the catalog (`unmatched_terms` missing); results are only similar items.
        `possible_accessory_query` = the query names an accessory the store doesn't sell."""
        filters = ProductFilters(
            query=query,
            category=category,
            min_price=_decimal(min_price),
            max_price=_decimal(max_price),
            only_available=only_available,
        )
        # Not blocking: "violão 7 cordas" is a valid instrument search.
        accessory = (
            {"possible_accessory_query": True} if mentions_accessory(query, category) else {}
        )
        try:
            found = repo.find_products(filters)
        except UnknownCategoryError as exc:
            return to_json({"error": "unknown_category", "categories": exc.known, **accessory})
        result: dict[str, Any] = {
            "total_matches": len(found.products),
            "products": [product_summary(p) for p in found.products[:limit]],
        }
        if found.partial_match:
            result["partial_match"] = True
            result["unmatched_terms"] = found.unmatched_terms
        if found.unavailable:
            result["unavailable"] = [
                {"id": p.id, "name": p.name, "availability": p.availability}
                for p in found.unavailable
            ]
        return to_json(result | accessory)

    @tool(args_schema=ProductIdArgs)
    def get_product_details(product_id: int) -> str:
        """Full specs and description of one product (id from search_products)."""
        product = repo.get_product(product_id)
        if product is None:
            return to_json({"error": "product_not_found"})
        data = product_summary(product, specs=product.specs)
        if product.description:  # unreliable descriptions are never shown (D2)
            data["description"] = product.description
        if product.availability == "disponivel":
            price, bands = product.final_price, client.payments.installment_bands
            n = max_installments(price, bands)
            data["payment"] = {
                **money("pix_price", pix_price(price, product.promotion is not None, payments)),
                "max_installments": n,
                **money("installment", to_cents(price / n)),
            }
        return to_json(data)

    @tool
    def list_active_promotions() -> str:
        """Promotions active today, with original and final price. Any other promotion
        (e.g. a past Black Friday) is not active."""
        return to_json({"promotions": [product_summary(p) for p in repo.list_active_promotions()]})

    @tool(args_schema=PaymentArgs)
    def calculate_payment_options(items: list[CartItem]) -> str:
        """Totals for a cart: card/debit/boleto total, PIX total, interest-free installments,
        combined payment. Use for any question about paying, PIX, installments ("parcelas")."""
        quantities: dict[int, int] = {}
        for item in items:
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

        lines, rows, not_for_sale = [], [], []
        for product_id, quantity in quantities.items():
            product = repo.get_product(product_id)
            if product is None:
                not_for_sale.append({"id": product_id, "reason": "product_not_found"})
            elif product.availability != "disponivel":
                not_for_sale.append(
                    {"id": product_id, "name": product.name, "reason": product.availability}
                )
            elif quantity > product.stock:
                not_for_sale.append(
                    {
                        "id": product_id,
                        "name": product.name,
                        "reason": "quantity_exceeds_stock",
                        "available_quantity": product.stock,
                    }
                )
            else:
                line = CartLine(product.final_price, quantity, product.promotion is not None)
                lines.append(line)
                row = {"id": product.id, "name": product.name, "quantity": quantity}
                row |= money("total", line.total)
                if product.promotion:
                    row |= {"promo_percent": product.promotion.percent, "pix_discount": False}
                rows.append(row)
        if not lines:
            return to_json({"error": "nothing_to_sell", "not_for_sale": not_for_sale})

        options = payment_options(lines, payments)
        result: dict[str, Any] = {
            "items": rows,
            **money("total", options.total),  # credit card, debit or boleto
            **money("pix_total", options.pix_total),
            "pix_discount_percent": payments.pix_discount_percent,
            "max_installments": options.max_installments,
            "installments": [f"{n}x de {format_brl(v)}" for n, v in options.installments],
            "boleto_clearing_business_days": payments.boleto_clearing_business_days,
            "combined_payment_allowed": options.combined_payment_allowed,
            "combined_payment_min_total_text": format_brl(
                Decimal(str(payments.combined_payment_min_total))
            ),
        }
        if not_for_sale:
            result["not_for_sale"] = not_for_sale
        return to_json(result)

    @tool(args_schema=OrderLookupArgs)
    def get_order_status(order_id: int, email: str, config: RunnableConfig) -> str:
        """Status of the customer's own order. Ask for the order number AND the purchase email
        first. delayed=true → apologize and offer handoff_to_human."""
        order = identified_order(order_id, email, config)
        if isinstance(order, str):
            return order
        eta = order.estimated_delivery
        today = clock.now().date()
        data: dict[str, Any] = {
            "order_id": order.order_id,
            "customer_first_name": order.customer_first_name,
            "status": order.status,
            "status_text": ORDER_STATUS_TEXT[order.status],
            "order_date": br_date(order.order_date),
            "items": [{"name": i.name, "quantity": i.quantity} for i in order.items],
            **money("total", order.total),  # amount paid (D4)
            "payment_method": payment_method_text(order.payment_method),
            "tracking_code": order.tracking_code,
            "estimated_delivery": br_date(eta),
            # D7: only orders on their way can be late
            "delayed": order.status in ("confirmed", "shipped") and eta is not None and eta < today,
            "notes": order.notes,
        }
        return to_json({k: v for k, v in data.items() if v is not None})

    @tool(args_schema=ReturnArgs)
    def check_return_eligibility(
        order_id: int, email: str, reason: ReturnReason, config: RunnableConfig
    ) -> str:
        """Whether a delivered order can be returned/exchanged today and until when.
        reason: arrependimento (regret) | defeito (defect) | preferencia (other color/model).
        Ask for order number AND purchase email first."""
        order = identified_order(order_id, email, config)
        if isinstance(order, str):
            return order
        decision = return_eligibility(
            reason,
            order.status,
            order.order_date,
            order.estimated_delivery,
            clock.now().date(),
            client.returns,
        )
        data: dict[str, Any] = {
            "order_id": order.order_id,
            "customer_first_name": order.customer_first_name,
            "reason": reason,
            "eligible": decision.eligible,
            "rule_applied": decision.rule_applied,
            "deadline": br_date(decision.deadline),
            "days_elapsed": decision.days_elapsed,
            "receipt_date": br_date(decision.receipt_date),
            "legal_warranty_until": br_date(decision.legal_warranty_until),
            "next_steps": RETURN_NEXT_STEPS[
                "eligible" if decision.eligible else decision.rule_applied
            ],
        }
        if decision.receipt_date:
            data["receipt_date_is_estimate"] = True  # D6: estimated delivery date
            data["conditions_to_confirm"] = client.returns.item_conditions[reason]
            data["not_eligible_items"] = client.returns.ineligible_conditions
        return to_json({k: v for k, v in data.items() if v is not None})

    return [
        search_products,
        get_product_details,
        list_active_promotions,
        calculate_payment_options,
        get_order_status,
        check_return_eligibility,
    ]
