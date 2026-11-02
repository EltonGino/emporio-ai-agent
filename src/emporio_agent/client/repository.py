"""Store data access. Tools only see the `StoreRepository` protocol and plain records.

`SqlStoreRepository` works on any SQLAlchemy database (SQLite here, Postgres in production).
The catalog is small, so fuzzy matching and promotional-price filters run in Python over the
SQL-filtered candidates; with a large catalog they would move to the database (trigram/FTS).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from emporio_agent.client import matching
from emporio_agent.client.db.loader import normalize_email
from emporio_agent.client.db.models import (
    Category,
    Handoff,
    Order,
    OrderItem,
    Product,
    Promotion,
)
from emporio_agent.client.rules import apply_discount, availability

# Extra search words derived from specs, so "violão de aço" or "eletroacústico" match.
SPEC_SEARCH_TERMS = {
    ("strings", "steel"): "aço",
    ("electronics", "yes"): "elétrico eletroacústico",
}
MAX_UNAVAILABLE_MATCHES = 3
PROTOCOL_PREFIX = "EMP"
PROTOCOL_ATTEMPTS = 3  # retries if two handoffs race for the same daily number


@dataclass(frozen=True)
class PromotionInfo:
    promotion_id: int
    percent: int
    name: str


@dataclass(frozen=True)
class ProductInfo:
    id: int
    name: str
    category: str
    price: Decimal  # list price
    status: str
    stock: int
    specs: dict[str, Any]
    description: str | None  # None when unreliable (D2): never shown
    promotion: PromotionInfo | None  # best active promotion (D3)

    @property
    def availability(self) -> str:
        return availability(self.status, self.stock)

    @property
    def final_price(self) -> Decimal:
        if self.promotion is None:
            return self.price
        return apply_discount(self.price, self.promotion.percent)


@dataclass(frozen=True)
class ProductFilters:
    query: str | None = None
    category: str | None = None
    min_price: Decimal | None = None  # compared with the final (promotional) price
    max_price: Decimal | None = None
    only_available: bool = True


@dataclass(frozen=True)
class ProductSearch:
    products: list[ProductInfo]  # ranked best first
    # With only_available: items matching the query that can't be sold now (to say so).
    unavailable: list[ProductInfo] = field(default_factory=list)
    # Partial match: no product has every query term; these terms are missing from some or
    # all of the results (e.g. ["preto"]), so the exact item is not in the catalog.
    unmatched_terms: list[str] = field(default_factory=list)

    @property
    def partial_match(self) -> bool:
        return bool(self.unmatched_terms)


@dataclass(frozen=True)
class OrderItemInfo:
    product_id: int
    name: str
    quantity: int


@dataclass(frozen=True)
class OrderInfo:
    """What an identified customer may see about their own order (no other customer data)."""

    order_id: int
    customer_first_name: str
    order_date: date
    status: str
    total: Decimal  # amount paid; may differ from the sum of items (D4)
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None  # also the proxy for the receipt date (D6)
    notes: str | None
    items: list[OrderItemInfo]


@dataclass(frozen=True)
class HandoffResult:
    protocol: str
    created: bool  # False: the same case was already open in this session (idempotent)


class UnknownCategoryError(ValueError):
    def __init__(self, name: str, known: list[str]) -> None:
        super().__init__(f"unknown category {name!r}")
        self.known = known


class StoreRepository(Protocol):
    def find_products(self, filters: ProductFilters) -> ProductSearch: ...

    def get_product(self, product_id: int) -> ProductInfo | None: ...

    def active_promotion(self, product_id: int) -> PromotionInfo | None: ...

    def list_active_promotions(self) -> list[ProductInfo]: ...

    def get_order_for_customer(self, order_id: int, email: str) -> OrderInfo | None:
        """None when the order doesn't exist or the email isn't its customer's (same answer)."""
        ...

    def create_handoff(
        self,
        reason: str,
        summary: str,
        contact: str | None,
        session_id: str | None,
        created_at: datetime,
        order_id: int | None = None,
    ) -> HandoffResult:
        """Register a request for the human team (protocol EMP-YYYYMMDD-NNNN). Idempotent per
        session + reason + order: the same case twice returns the open protocol."""
        ...


def best_promotion(promotions: Sequence[Promotion]) -> PromotionInfo | None:
    """D3: the active flag is the truth; if several are active, the highest applies."""
    active = [p for p in promotions if p.is_active]
    if not active:
        return None
    best = max(active, key=lambda p: (p.discount_percent, -p.promotion_id))
    return PromotionInfo(best.promotion_id, best.discount_percent, best.description)


def _to_info(product: Product) -> ProductInfo:
    return ProductInfo(
        id=product.product_id,
        name=product.name,
        category=product.category.name,
        price=product.price_brl,
        status=product.status,
        stock=product.stock_quantity,
        specs=dict(product.specs),
        description=product.description if product.description_reliable else None,
        promotion=best_promotion(product.promotions),
    )


def _search_text(product: ProductInfo) -> set[str]:
    words = [product.name, product.category]
    for key, value in product.specs.items():
        words.append(SPEC_SEARCH_TERMS.get((key, str(value)), str(value)))
    return matching.document_terms(" ".join(words))


def _rank(terms: list[str], candidates: list[ProductInfo]) -> tuple[list[ProductInfo], list[str]]:
    """Products matching every term; if none, those matching at least half of the terms
    (partial match). Ranked by match, then by final price (cheapest first).
    Also returns the terms some returned product lacks (empty for a full match)."""
    docs = {p.id: _search_text(p) for p in candidates}
    scored = [(matching.match_score(terms, docs[p.id]), p) for p in candidates]
    full = [(s, p) for s, p in scored if s == 1.0]
    chosen = full or [(s, p) for s, p in scored if s >= 0.5]
    chosen.sort(key=lambda sp: (-sp[0], sp[1].final_price, sp[1].id))
    products = [p for _, p in chosen]
    unmatched = [
        t for t in terms if not all(matching.term_matches(t, docs[p.id]) for p in products)
    ]
    return products, unmatched


def _matching_categories(categories: list[Category], name: str) -> set[str]:
    """Categories whose name (or, failing that, description) matches every term."""
    terms = matching.query_terms(name)
    for field_name in ("name", "description"):
        found = {
            c.name
            for c in categories
            if terms
            and matching.match_score(terms, matching.document_terms(getattr(c, field_name))) == 1.0
        }
        if found:
            return found
    raise UnknownCategoryError(name, [c.name for c in categories])


def _named_categories(categories: list[Category], terms: list[str]) -> set[str]:
    """Categories named by a query word ("ukulele preto" → Ukuleles)."""
    return {
        c.name
        for c in categories
        if any(matching.term_matches(t, matching.document_terms(c.name)) for t in terms)
    }


class SqlStoreRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _products(self, session: Session, *conditions: Any) -> list[ProductInfo]:
        stmt = (
            select(Product)
            .options(selectinload(Product.category), selectinload(Product.promotions))
            .where(*conditions)
            .order_by(Product.product_id)
        )
        return [_to_info(p) for p in session.scalars(stmt)]

    def find_products(self, filters: ProductFilters) -> ProductSearch:
        with Session(self._engine) as session:
            categories = list(session.scalars(select(Category).order_by(Category.category_id)))
            candidates = self._products(session)

        if filters.category:
            allowed = _matching_categories(categories, filters.category)
            candidates = [p for p in candidates if p.category in allowed]
        candidates = [
            p
            for p in candidates
            if (filters.min_price is None or p.final_price >= filters.min_price)
            and (filters.max_price is None or p.final_price <= filters.max_price)
        ]
        terms = matching.query_terms(filters.query or "")
        # A query word naming a category acts as a filter, so alternatives for "ukulele preto"
        # are ukuleles; skipped when those categories are empty ("cordas" → Cordas Orquestrais).
        named = _named_categories(categories, terms)
        in_named = [p for p in candidates if p.category in named]
        if in_named:
            candidates = in_named

        unavailable: list[ProductInfo] = []
        if filters.only_available:
            matches, partial = _rank(terms, candidates)
            if terms and not partial:  # the exact item exists but can't be sold now: say so
                unavailable = [p for p in matches if p.availability != "disponivel"]
            candidates = [p for p in candidates if p.availability == "disponivel"]
        products, unmatched = _rank(terms, candidates)
        return ProductSearch(products, unavailable[:MAX_UNAVAILABLE_MATCHES], unmatched)

    def get_product(self, product_id: int) -> ProductInfo | None:
        with Session(self._engine) as session:
            found = self._products(session, Product.product_id == product_id)
        return found[0] if found else None

    def active_promotion(self, product_id: int) -> PromotionInfo | None:
        with Session(self._engine) as session:
            promotions = session.scalars(
                select(Promotion).where(Promotion.product_id == product_id)
            ).all()
            return best_promotion(promotions)

    def list_active_promotions(self) -> list[ProductInfo]:
        with Session(self._engine) as session:
            active = select(Promotion.product_id).where(Promotion.is_active)
            products = self._products(session, Product.product_id.in_(active))
        return sorted(products, key=lambda p: (p.final_price / p.price, p.id))  # biggest first

    def get_order_for_customer(self, order_id: int, email: str) -> OrderInfo | None:
        with Session(self._engine) as session:
            order = session.scalars(
                select(Order)
                .options(
                    selectinload(Order.customer),
                    selectinload(Order.items).selectinload(OrderItem.product),
                )
                .where(Order.order_id == order_id)
            ).one_or_none()
            # The email must be the one of this order's own customer (D8: emails aren't unique).
            if order is None or order.customer.email != normalize_email(email):
                return None
            return OrderInfo(
                order_id=order.order_id,
                customer_first_name=order.customer.name.split()[0],
                order_date=order.order_date,
                status=order.status,
                total=order.total_brl,
                payment_method=order.payment_method,
                tracking_code=order.tracking_code,
                estimated_delivery=order.estimated_delivery,
                notes=order.notes,
                items=[
                    OrderItemInfo(i.product_id, i.product.name, i.quantity)
                    for i in sorted(order.items, key=lambda i: i.id)
                ],
            )

    def create_handoff(
        self,
        reason: str,
        summary: str,
        contact: str | None,
        session_id: str | None,
        created_at: datetime,
        order_id: int | None = None,
    ) -> HandoffResult:
        prefix = f"{PROTOCOL_PREFIX}-{created_at:%Y%m%d}-"
        for _ in range(PROTOCOL_ATTEMPTS):
            try:
                with Session(self._engine) as session, session.begin():
                    if session_id is not None:  # no session → nothing to deduplicate against
                        existing = session.scalar(
                            select(Handoff.protocol).where(
                                Handoff.session_id == session_id,
                                Handoff.reason == reason,
                                Handoff.order_id.is_not_distinct_from(order_id),
                                Handoff.status == "open",
                            )
                        )
                        if existing:
                            return HandoffResult(existing, created=False)
                    today = session.scalar(
                        select(func.count()).where(Handoff.protocol.startswith(prefix))
                    )
                    protocol = f"{prefix}{today + 1:04d}"
                    session.add(
                        Handoff(
                            protocol=protocol,
                            created_at=created_at.replace(tzinfo=None),
                            session_id=session_id,
                            reason=reason,
                            order_id=order_id,
                            summary=summary,
                            contact=contact,
                        )
                    )
                return HandoffResult(protocol, created=True)
            except IntegrityError:
                continue  # another handoff took this number: count again
        raise RuntimeError("could not allocate a handoff protocol")
