"""Store data access. Tools only see the `StoreRepository` protocol and plain records.

`SqlStoreRepository` works on any SQLAlchemy database (SQLite here, Postgres in production).
The catalog is small, so fuzzy matching and promotional-price filters run in Python over the
SQL-filtered candidates; with a large catalog they would move to the database (trigram/FTS).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload

from emporio_agent.client import matching
from emporio_agent.client.db.models import Category, Product, Promotion
from emporio_agent.client.rules import apply_discount, availability

# Extra search words derived from specs, so "violão de aço" or "eletroacústico" match.
SPEC_SEARCH_TERMS = {
    ("strings", "steel"): "aço",
    ("electronics", "yes"): "elétrico eletroacústico",
}
MAX_UNAVAILABLE_MATCHES = 3


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


class UnknownCategoryError(ValueError):
    def __init__(self, name: str, known: list[str]) -> None:
        super().__init__(f"unknown category {name!r}")
        self.known = known


class StoreRepository(Protocol):
    def find_products(self, filters: ProductFilters) -> ProductSearch: ...

    def get_product(self, product_id: int) -> ProductInfo | None: ...

    def active_promotion(self, product_id: int) -> PromotionInfo | None: ...

    def list_active_promotions(self) -> list[ProductInfo]: ...


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


def _best_matches(scored: list[tuple[float, ProductInfo]]) -> list[ProductInfo]:
    """Products matching every term; if none, those matching at least half of the terms.
    Ranked by match, then by final price (cheapest first)."""
    full = [(s, p) for s, p in scored if s == 1.0]
    chosen = full or [(s, p) for s, p in scored if s >= 0.5]
    chosen.sort(key=lambda sp: (-sp[0], sp[1].final_price, sp[1].id))
    return [p for _, p in chosen]


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

    def _category_ids(self, session: Session, name: str) -> list[int]:
        """Categories whose name (or, failing that, description) matches every term."""
        categories = list(session.scalars(select(Category).order_by(Category.category_id)))
        terms = matching.query_terms(name)
        for field_name in ("name", "description"):
            ids = [
                c.category_id
                for c in categories
                if matching.match_score(terms, matching.document_terms(getattr(c, field_name)))
                == 1.0
            ]
            if terms and ids:
                return ids
        raise UnknownCategoryError(name, [c.name for c in categories])

    def find_products(self, filters: ProductFilters) -> ProductSearch:
        with Session(self._engine) as session:
            conditions = []
            if filters.category:
                ids = self._category_ids(session, filters.category)
                conditions.append(Product.category_id.in_(ids))
            candidates = self._products(session, *conditions)

        candidates = [
            p
            for p in candidates
            if (filters.min_price is None or p.final_price >= filters.min_price)
            and (filters.max_price is None or p.final_price <= filters.max_price)
        ]
        terms = matching.query_terms(filters.query or "")
        scored = [(matching.match_score(terms, _search_text(p)), p) for p in candidates]
        if not filters.only_available:
            return ProductSearch(_best_matches(scored))

        available = [(s, p) for s, p in scored if p.availability == "disponivel"]
        unavailable = []
        if terms:  # a named search for something we can't sell now is worth reporting
            unavailable = [p for p in _best_matches(scored) if p.availability != "disponivel"]
        return ProductSearch(_best_matches(available), unavailable[:MAX_UNAVAILABLE_MATCHES])

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
