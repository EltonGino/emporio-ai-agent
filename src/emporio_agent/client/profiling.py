"""Data-quality profiling of the raw inputs (AGENTS.md §5.3).

Each check reads `data/raw` directly, finds the evidence for one finding and pairs it with the
decision taken. `scripts/profile_data.py` prints them; the README "Data handling" section is
written from that output.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from emporio_agent.client.db.loader import (
    RAW_DIR,
    UNRELIABLE_DESCRIPTION_IDS,
    normalize_email,
    read_csv,
)
from emporio_agent.config import ClientConfig, get_client_config

POLICY_PDF = "politicas_da_loja.pdf"

# Brands found in product names/descriptions, plus model names that imply a brand.
BRANDS = (
    "Yamaha", "Tagima", "Giannini", "Rozini", "Shelby", "Crafter", "Martin", "Taylor",
    "Takamine", "Kala", "Ohana", "Kalani", "Fender", "Gibson", "Ibanez", "PRS", "Music Man",
    "Pearl", "Tama", "Korg", "Nord", "Roland",
)  # fmt: skip
MODEL_BRANDS = {"Jazz Bass": "Fender", "Precision": "Fender", "StingRay": "Music Man"}
# Description phrase → specs key it must agree with.
COUNT_PHRASES = {r"(\d+)\s+teclas": "keys", r"(\d+)\s+cordas": "strings"}


@dataclass
class Finding:
    id: str
    title: str
    decision: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class RawData:
    categories: list[dict[str, str]]
    products: list[dict[str, str]]
    customers: list[dict[str, str]]
    orders: list[dict[str, str]]
    order_items: list[dict[str, str]]
    promotions: list[dict[str, str]]
    policy_text: str  # whitespace-normalized

    @classmethod
    def load(cls, raw_dir: Path = RAW_DIR) -> RawData:
        tables = {
            name: read_csv(raw_dir / f"{name}.csv")
            for name in ("categories", "products", "customers", "orders", "order_items")
        }
        pages = PdfReader(raw_dir / POLICY_PDF).pages
        text = " ".join(" ".join(page.extract_text().split()) for page in pages)
        return cls(**tables, promotions=read_csv(raw_dir / "promotions.csv"), policy_text=text)

    def product(self, product_id: str) -> dict[str, str]:
        return next(p for p in self.products if p["product_id"] == product_id)


def _label(product: dict[str, str]) -> str:
    return f"product {product['product_id']} '{product['name']}'"


def check_availability(data: RawData) -> Finding:
    finding = Finding(
        "D1",
        "Availability: status and stock disagree",
        "available = status 'active' AND stock > 0; status wins over stock. Discontinued → "
        "not for sale; coming_soon → 'em breve'; active with 0 → out of stock, suggest others.",
    )
    for p in data.products:
        stock = int(p["stock_quantity"])
        if (p["status"] != "active") or stock == 0:
            finding.evidence.append(f"{_label(p)}: status={p['status']}, stock={stock}")
    return finding


def _brands_in(text: str) -> set[str]:
    found = {b for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", text)}
    found |= {brand for model, brand in MODEL_BRANDS.items() if model in text}
    return found


def check_descriptions(data: RawData) -> Finding:
    finding = Finding(
        "D2",
        "Descriptions contradict product names/specs",
        "structured columns are the truth; descriptions of the synthetic batch 135–144 are "
        "flagged description_reliable=False and never shown to the LLM.",
    )
    for p in data.products:
        problems = []
        foreign = _brands_in(p["description"]) - _brands_in(p["name"])
        if foreign:
            problems.append(f"description names {', '.join(sorted(foreign))}")
        specs = json.loads(p["specs"]) if p["specs"] else {}
        for pattern, key in COUNT_PHRASES.items():
            match = re.search(pattern, p["description"])
            # Only numeric specs are counts (violões use strings=nylon/steel for the type).
            spec = str(specs.get(key, ""))
            if match and spec.isdigit() and match.group(1) != spec:
                problems.append(f"description says {match.group(0)}, specs {key}={specs[key]}")
        if problems:
            finding.evidence.append(f"{_label(p)}: {'; '.join(problems)}")
    undetected = sorted(UNRELIABLE_DESCRIPTION_IDS - {int(e.split()[1]) for e in finding.evidence})
    if undetected:
        finding.evidence.append(
            f"also distrusted (same batch, generic names, no explicit contradiction): "
            f"{', '.join(map(str, undetected))}"
        )
    return finding


def check_promotions(data: RawData) -> Finding:
    finding = Finding(
        "D3",
        "Promotions have no start/end dates",
        "the is_active flag is the truth; if a product ever had more than one active "
        "promotion, apply the highest (non-cumulative).",
    )
    columns = list(data.promotions[0])
    finding.evidence.append(f"promotion columns: {', '.join(columns)} (no dates)")
    for promo in data.promotions:
        if promo["is_active"] == "1":
            product = data.product(promo["product_id"])
            finding.evidence.append(
                f"active: {_label(product)} {promo['discount_percent']}% ({promo['description']})"
            )
    per_product = Counter(p["product_id"] for p in data.promotions)
    active_per_product = Counter(p["product_id"] for p in data.promotions if p["is_active"] == "1")
    repeated = sorted((pid for pid, n in per_product.items() if n > 1), key=int)
    finding.evidence.append(f"products with several promotions: {', '.join(repeated)}")
    finding.evidence.append(
        f"max active promotions per product: {max(active_per_product.values(), default=0)}"
    )
    return finding


def check_order_totals(data: RawData) -> Finding:
    finding = Finding(
        "D4",
        "Order totals differ from the sum of their items",
        "total_brl is the amount paid (discounts are not itemized); always report total_brl.",
    )
    prices = {p["product_id"]: Decimal(p["price_brl"]) for p in data.products}
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for item in data.order_items:
        sums[item["order_id"]] += prices[item["product_id"]] * int(item["quantity"])
    for order in data.orders:
        total = Decimal(order["total_brl"])
        if sums[order["order_id"]] != total:
            finding.evidence.append(
                f"order {order['order_id']}: total_brl={total}, "
                f"items at catalog price={sums[order['order_id']]}"
            )
    return finding


def _max_installments(total: Decimal, client: ClientConfig) -> int:
    allowed = 1
    for band in client.payments.installment_bands:
        for n in range(band.min_installments, band.max_installments + 1):
            if n == 1 or total / n >= Decimal(str(band.min_installment)):
                allowed = n
    return allowed


def check_historical_installments(data: RawData, client: ClientConfig) -> Finding:
    finding = Finding(
        "D5",
        "Past orders used installments the current rules don't allow",
        "assume the policy changed (manual v2.1); rules apply to new purchases only; never "
        "flag past orders to customers.",
    )
    for order in data.orders:
        match = re.fullmatch(r"credit_(\d+)x", order["payment_method"])
        if not match:
            continue
        used, total = int(match.group(1)), Decimal(order["total_brl"])
        allowed = _max_installments(total, client)
        if used > allowed:
            finding.evidence.append(
                f"order {order['order_id']}: R$ {total} in {used}x "
                f"(R$ {total / used:.2f} each); today max {allowed}x"
            )
    return finding


def check_delivery_date(data: RawData) -> Finding:
    finding = Finding(
        "D6",
        "No delivery (receipt) date column",
        "estimated_delivery is the proxy for the receipt date in return-window math; tool "
        "output says the date is an estimate.",
    )
    finding.evidence.append(f"order columns: {', '.join(data.orders[0])}")
    delivered = [o["order_id"] for o in data.orders if o["status"] == "delivered"]
    finding.evidence.append(f"delivered orders relying on the proxy: {', '.join(delivered)}")
    return finding


def check_stale_orders(data: RawData) -> Finding:
    finding = Finding(
        "D7",
        "Stale orders relative to the last order in the data",
        "order tool returns delayed=true when the ETA is before today → apologize and offer "
        "a human handoff. Demo date 2026-03-25 sits right after the last order.",
    )
    last = max(date.fromisoformat(o["order_date"]) for o in data.orders)
    finding.evidence.append(f"last order date: {last}")
    for order in data.orders:
        ordered = date.fromisoformat(order["order_date"])
        eta = order["estimated_delivery"]
        if order["status"] == "shipped" and eta and date.fromisoformat(eta) < last:
            finding.evidence.append(f"order {order['order_id']}: shipped, ETA {eta} already past")
        elif order["status"] in ("pending", "confirmed"):
            age = (last - ordered).days
            finding.evidence.append(
                f"order {order['order_id']}: {order['status']} for {age} days (since {ordered})"
            )
    return finding


def check_duplicate_emails(data: RawData) -> Finding:
    finding = Finding(
        "D8",
        "Customers sharing an email",
        "identity check matches the email of the order's own customer, so this is harmless; "
        "noted only.",
    )
    by_email: dict[str, list[str]] = defaultdict(list)
    for customer in data.customers:
        by_email[normalize_email(customer["email"])].append(customer["customer_id"])
    for email, ids in by_email.items():
        if len(ids) > 1:
            finding.evidence.append(f"{email}: customers {', '.join(ids)}")
    return finding


def check_empty_categories(data: RawData) -> Finding:
    finding = Finding(
        "D9",
        "Categories without products",
        "never invent items; say the store has none in the catalog right now.",
    )
    used = {p["category_id"] for p in data.products}
    for category in data.categories:
        if category["category_id"] not in used:
            finding.evidence.append(f"category {category['category_id']} '{category['name']}'")
    return finding


def check_specs(data: RawData) -> Finding:
    finding = Finding(
        "D10",
        "specs is JSON with category-dependent keys",
        "parsed to a dict per product; tools show the keys present for that category.",
    )
    keys: dict[str, set[str]] = defaultdict(set)
    for p in data.products:
        keys[p["category_id"]] |= set(json.loads(p["specs"]) if p["specs"] else {})
    names = {c["category_id"]: c["name"] for c in data.categories}
    for category_id in sorted(keys, key=int):
        finding.evidence.append(f"{names[category_id]}: {', '.join(sorted(keys[category_id]))}")
    return finding


def check_non_active_in_orders(data: RawData) -> Finding:
    finding = Finding(
        "E1",
        "Products not for sale appear in orders",
        "status wins for new sales (D1); past orders are reported as they are.",
    )
    status = {p["product_id"]: p["status"] for p in data.products}
    orders: dict[str, list[str]] = defaultdict(list)
    for item in data.order_items:
        if status[item["product_id"]] != "active":
            orders[item["product_id"]].append(item["order_id"])
    order_status = {o["order_id"]: o["status"] for o in data.orders}
    for product_id, order_ids in orders.items():
        listed = ", ".join(f"{oid} ({order_status[oid]})" for oid in order_ids)
        finding.evidence.append(
            f"{_label(data.product(product_id))} [{status[product_id]}] in orders {listed}"
        )
    return finding


def check_policy_phones(data: RawData) -> Finding:
    finding = Finding(
        "P1",
        "Manual gives two WhatsApp numbers",
        "use (67) 3341-4444: it appears twice, including the official company data table.",
    )
    raw = re.findall(r"\(\d{2}\)\s*\d{4}\s*-\s*\d{4}", data.policy_text)
    counts = Counter(re.sub(r"\s*-\s*", "-", number) for number in raw)
    for number, count in counts.most_common():
        finding.evidence.append(f"{number}: {count} occurrence(s)")
    return finding


def check_policy_emails(data: RawData) -> Finding:
    finding = Finding(
        "P2",
        "Manual email with an accent (typo)",
        "use the unaccented contato@emporiodamusica.com.br.",
    )
    for email, count in Counter(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", data.policy_text)).items():
        note = " ← non-ASCII" if not email.isascii() else ""
        finding.evidence.append(f"{email}: {count} occurrence(s){note}")
    return finding


def check_policy_installments(data: RawData) -> Finding:
    finding = Finding(
        "P3",
        "Minimum installment: flat value in §3 table vs bands in §3.1",
        "bands win (more specific); 'sem valor mínimo (exceto abaixo de R$ 50)' read as a "
        "R$ 50 minimum for 2–3x; 1x always allowed.",
    )
    for sentence in re.findall(
        r"[^.•]*(?:parcela mínima|exceto abaixo)[^•]*?R\$ ?[\d.]+,\d{2}", data.policy_text, re.I
    ):
        finding.evidence.append(sentence.strip()[-110:])
    return finding


def check_partner_stores(data: RawData, client: ClientConfig) -> Finding:
    finding = Finding(
        "P4",
        "Manual mentions partner stores but names none",
        "never invent store names; suggest partners only if catalog.partner_stores is set.",
    )
    for sentence in re.findall(r"[^.]*parceir[^.]*\.", data.policy_text, re.I):
        finding.evidence.append(f"manual: '{sentence.strip()}'")
    finding.evidence.append(f"client.yaml partner_stores: {client.catalog.partner_stores}")
    return finding


def run_checks(data: RawData | None = None, client: ClientConfig | None = None) -> list[Finding]:
    data = data or RawData.load()
    client = client or get_client_config()
    return [
        check_availability(data),
        check_descriptions(data),
        check_promotions(data),
        check_order_totals(data),
        check_historical_installments(data, client),
        check_delivery_date(data),
        check_stale_orders(data),
        check_duplicate_emails(data),
        check_empty_categories(data),
        check_specs(data),
        check_non_active_in_orders(data),
        check_policy_phones(data),
        check_policy_emails(data),
        check_policy_installments(data),
        check_partner_stores(data, client),
    ]
