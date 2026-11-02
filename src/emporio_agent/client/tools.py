"""LangChain tools: thin wrappers over the repository and rules.

Outputs are compact JSON with English keys; money comes as a float plus a pt-BR string
(`price` / `price_text`) so the LLM never computes or formats it. Docstrings are the tool
descriptions the LLM reads: short, saying when to use each tool (token budget).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from emporio_agent.client import identity, matching
from emporio_agent.client.db.models import ORDER_HANDOFF_REASONS
from emporio_agent.client.repository import (
    OrderInfo,
    ProductFilters,
    ProductInfo,
    StoreRepository,
    UnknownCategoryError,
)
from emporio_agent.client.rules import (
    CartLine,
    HolidayCalendar,
    StoreStatus,
    format_brl,
    max_installments,
    payment_options,
    pix_price,
    return_eligibility,
    store_status,
    to_cents,
)
from emporio_agent.clock import Clock
from emporio_agent.config import WEEKDAYS, ClientConfig, ReturnReason
from emporio_agent.core.retrieval.base import Retriever

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

WEEKDAY_NAMES = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")

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
    query: str = Field("", description="Name, brand, model or feature")
    category: str = Field("", description="e.g. violões, ukulele")
    min_price: float = Field(0, ge=0, description="BRL, final price")
    max_price: float = Field(0, ge=0, description="BRL, final price; 0 = no limit")
    only_available: bool = True
    on_promotion: bool = Field(False, description="Only items with an active promotion")
    limit: int = Field(5, ge=1, le=10)


class ProductIdArgs(BaseModel):
    product_id: int


class CartItem(BaseModel):
    product_id: int
    quantity: int = Field(1, ge=1, le=20)


class PaymentArgs(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=10)


class OrderLookupArgs(BaseModel):
    # Optional on purpose: a call without them gets guidance instead of an HTTP 400 from the
    # provider's schema validation (seen live: order_id 0 / empty email before asking).
    order_id: int = Field(0, ge=0, description="Order number the customer gave")
    email: str = Field("", max_length=254, description="Purchase email the customer gave")


class ReturnArgs(OrderLookupArgs):
    reason: ReturnReason


def br_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


class PolicyQueryArgs(BaseModel):
    query: str = Field(min_length=3, max_length=300)


class HandoffArgs(BaseModel):
    reason: Literal[
        "complaint",
        "delayed_order",
        "return_request",
        "manager_question",
        "unavailable_item",
        "other",
    ]
    summary: str = Field(min_length=10, max_length=600, description="For the team, in Portuguese")
    contact: str = Field("", max_length=120, description="Customer's own phone/email, if given")
    order_id: int = Field(0, ge=0, description="Verified order number, if about an order")


def br_datetime(value: datetime) -> str:
    """e.g. "quarta-feira, 25/03/2026 às 10:30" (weekdays from Monday = 0)."""
    weekday = WEEKDAY_NAMES[value.weekday()]
    suffix = "" if value.weekday() >= 5 else "-feira"
    return f"{weekday}{suffix}, {value:%d/%m/%Y} às {value:%H:%M}"


def hours_text(span: tuple[time, time] | None) -> str:
    return f"{span[0]:%H:%M}–{span[1]:%H:%M}" if span else "fechado"


def store_status_data(status: StoreStatus) -> dict[str, Any]:
    data: dict[str, Any] = {
        "now": br_datetime(status.now),
        "is_open": status.is_open,
        "today_hours": hours_text(status.today_hours),
        "holiday": status.holiday,
        "closes_at": f"{status.closes_at:%H:%M}" if status.closes_at else None,
        "next_opening": br_datetime(status.next_opening) if status.next_opening else None,
    }
    return {k: v for k, v in data.items() if v is not None}


def payment_method_text(method: str) -> str:
    if match := re.fullmatch(r"credit_(\d+)x", method):
        return f"cartão de crédito em {match.group(1)}x"
    return PAYMENT_METHOD_TEXT.get(method, method)


def _limit(value: float) -> Decimal | None:
    """0 means "no limit" (keeps the tool schema free of nullable fields)."""
    return Decimal(str(value)) if value else None


def _compact_description(tool_: BaseTool) -> BaseTool:
    """Docstrings → one line: the indentation would otherwise be sent (and billed) per call."""
    tool_.description = " ".join(tool_.description.split())
    return tool_


def build_tools(
    repo: StoreRepository,
    client: ClientConfig,
    clock: Clock,
    retriever: Retriever,
    failed_lookups: identity.FailedLookups | None = None,
    verified_orders: identity.VerifiedOrders | None = None,
) -> list[BaseTool]:
    payments = client.payments
    calendar = HolidayCalendar(client.store.holidays)
    weekly_hours = {
        WEEKDAY_NAMES[i]: hours_text(client.store.hours[day]) for i, day in enumerate(WEEKDAYS)
    } | {"feriados": "fechado"}
    failed_lookups = failed_lookups or identity.FailedLookups()
    verified_orders = verified_orders or identity.VerifiedOrders()

    def identified_order(order_id: int, email: str, config: RunnableConfig) -> OrderInfo | str:
        """The order, or the error JSON to return. Same error for unknown order / wrong email."""
        if not order_id or not email.strip():
            return identity.ORDER_DATA_MISSING  # not a failed attempt: nothing was checked
        session = identity.session_id(config)
        if session is None:
            return identity.NO_SESSION
        if failed_lookups.locked(session):
            return identity.TOO_MANY_ATTEMPTS
        order = repo.get_order_for_customer(order_id, email)
        if order is None:
            failed_lookups.record_failure(session)
            return identity.ORDER_NOT_FOUND
        verified_orders.record(session, order.order_id)  # allows order-related handoffs
        return order

    accessory_terms = matching.document_terms(" ".join(client.catalog.non_sold_accessories))

    def mentions_accessory(*texts: str | None) -> bool:
        terms = matching.query_terms(" ".join(t for t in texts if t))
        return any(matching.term_matches(t, accessory_terms) for t in terms)

    @tool(args_schema=SearchProductsArgs)
    def search_products(
        query: str = "",
        category: str = "",
        min_price: float = 0,
        max_price: float = 0,
        only_available: bool = True,
        on_promotion: bool = False,
        limit: int = 5,
    ) -> str:
        """Catalog search (price, promotion, availability); use for any product question.
        Shows `limit` of total_matches: say nothing about items not shown. availability:
        disponivel | esgotado | descontinuado | em_breve (not for sale yet). unavailable =
        exact matches not for sale now; offer only the returned alternatives. partial_match =
        exact item not in the catalog (unmatched_terms); results are only similar.
        possible_accessory_query = asks for an accessory the store doesn't sell."""
        filters = ProductFilters(
            query=query,
            category=category,
            min_price=_limit(min_price),
            max_price=_limit(max_price),
            only_available=only_available,
            on_promotion=on_promotion,
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
        if found.alternatives:
            result["alternatives"] = [product_summary(p) for p in found.alternatives]
        return to_json(result | accessory)

    @tool(args_schema=ProductIdArgs)
    def get_product_details(product_id: int) -> str:
        """Full specs, description and payment summary of a product (id from search)."""
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
        """All promotions active now, any category. Use for any promotion/discount question
        before saying what is or isn't on sale. Any other (e.g. a past Black Friday) is not
        active."""
        return to_json({"promotions": [product_summary(p) for p in repo.list_active_promotions()]})

    @tool(args_schema=PaymentArgs)
    def calculate_payment_options(items: list[CartItem]) -> str:
        """Cart totals: card/debit/boleto, PIX, interest-free installments (say
        installment_summary), combined payment. Use for any payment question."""
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
            "installment_summary": (
                f"até {options.max_installments}x sem juros de "
                f"{format_brl(options.installments[-1][1])}"
            ),
            # Every allowed option, for "e em 10x?"; the summary is what to say by default.
            "installment_options": [f"{n}x de {format_brl(v)}" for n, v in options.installments],
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
    def get_order_status(config: RunnableConfig, order_id: int = 0, email: str = "") -> str:
        """The customer's own order. Call only with the order number and purchase email the
        customer gave.
        estimated_delivery is a forecast; days_since_order counts from the purchase. delayed
        (see days_since_estimated_delivery) or long pending/confirmed → acknowledge the wait,
        offer the team, never invent dates."""
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
            "days_since_order": (today - order.order_date).days,
            "items": [{"name": i.name, "quantity": i.quantity} for i in order.items],
            **money("total", order.total),  # amount paid (D4)
            "payment_method": payment_method_text(order.payment_method),
            "tracking_code": order.tracking_code,
            "estimated_delivery": br_date(eta),
            "notes": order.notes,
        }
        # D7: only orders on their way can be late
        if order.status in ("confirmed", "shipped") and eta is not None and eta < today:
            data |= {"delayed": True, "days_since_estimated_delivery": (today - eta).days}
        else:
            data["delayed"] = False
        return to_json({k: v for k, v in data.items() if v is not None})

    @tool(args_schema=ReturnArgs, response_format="content_and_artifact")
    def check_return_eligibility(
        reason: ReturnReason, config: RunnableConfig, order_id: int = 0, email: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Can a delivered order be returned/exchanged today, and until when (deadline,
        days_left). reason: arrependimento | defeito | preferencia (other color/model). Needs
        order number and purchase email the customer gave."""
        order = identified_order(order_id, email, config)
        if isinstance(order, str):
            return order, {}
        today = clock.now().date()
        decision = return_eligibility(
            reason,
            order.status,
            order.order_date,
            order.estimated_delivery,
            today,
            client.returns,
        )
        data: dict[str, Any] = {
            "order_id": order.order_id,
            "customer_first_name": order.customer_first_name,
            "reason": reason,
            "eligible": decision.eligible,
            "rule_applied": decision.rule_applied,
            "deadline": br_date(decision.deadline),
            # Days left, not days elapsed: the model mixed up "3 days since receipt" with the
            # time left. 0 = today is the last day.
            "days_left": (decision.deadline - today).days if decision.eligible else None,
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
        # The artifact is not sent to the model: it is for tracing.
        return to_json({k: v for k, v in data.items() if v is not None}), {
            "days_elapsed": decision.days_elapsed
        }

    @tool
    def get_store_status() -> str:
        """Open/closed now, today's and weekly hours, next opening (holidays included)."""
        status = store_status(clock.now(), client.store, calendar)
        return to_json(store_status_data(status) | {"weekly_hours": weekly_hours})

    @tool(args_schema=PolicyQueryArgs, response_format="content_and_artifact")
    def search_policies(query: str) -> tuple[str, list[dict[str, Any]]]:
        """Policy manual search: returns, warranty, shipping, payment, promotions, privacy.
        Query: self-contained and policy-oriented, not the customer's words ("quanto demora pra
        Dourados?" → "prazo de entrega para outras cidades fora de Campo Grande"). Contacts in
        results may be outdated: use the official contacts."""
        hits = retriever.search(query, client.retrieval.top_k)
        content = to_json(
            {"results": [{"section": h.chunk.section_path, "text": h.chunk.text} for h in hits]}
        )
        # The artifact is not sent to the model: it feeds tracing and the demo panel.
        artifact = [
            {
                "doc": h.chunk.doc,
                "section_id": h.chunk.section_id,
                "section_path": h.chunk.section_path,
                "page": h.chunk.page,
                "score": round(h.score, 4),
                "matched_by": list(h.matched_by),
            }
            for h in hits
        ]
        return content, artifact

    @tool(args_schema=HandoffArgs)
    def handoff_to_human(
        reason: str,
        summary: str,
        config: RunnableConfig,
        contact: str = "",
        order_id: int = 0,
    ) -> str:
        """Open a case for the human team; returns the protocol. Ask the customer first
        (complaints can be registered directly). delayed_order and return_request need an
        order already looked up with number + email in this conversation. The same case again
        returns the existing protocol."""
        session = identity.session_id(config)
        # 0 / "" mean "not given". An order (required for order reasons) must be verified.
        if (reason in ORDER_HANDOFF_REASONS or order_id) and not verified_orders.verified(
            session, order_id
        ):
            return identity.ORDER_NOT_VERIFIED
        now = clock.now()
        result = repo.create_handoff(
            reason,
            summary,
            contact.strip() or None,
            session,
            created_at=now,
            order_id=order_id or None,
        )
        status = store_status(now, client.store, calendar)
        data: dict[str, Any] = {
            "protocol": result.protocol,
            "return_promise": (f"até {client.support.complaint_return_business_hours} horas úteis"),
            "team_available_now": status.is_open,
        }
        if not result.created:
            data["already_registered"] = True  # tell the customer it is the same case
        if status.next_opening:
            data["team_back_at"] = br_datetime(status.next_opening)
        return to_json(data)

    tools = [
        search_products,
        get_product_details,
        list_active_promotions,
        calculate_payment_options,
        get_order_status,
        check_return_eligibility,
        get_store_status,
        search_policies,
        handoff_to_human,
    ]
    return [_compact_description(t) for t in tools]
