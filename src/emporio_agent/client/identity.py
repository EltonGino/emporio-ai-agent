"""Identity check for order lookups: order number + purchase email, with a per-session lockout.

The session id comes from the tool's run config (`configurable.thread_id`, set by the agent's
caller), never from a tool argument, so the model can't reset or choose the counter.

One customer per conversation: the first successful check binds the session to that order's
customer; later lookups only open that customer's orders (owner's test: orders of three
customers opened in one session with number + email alone).
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from emporio_agent.client.db.models import SessionCustomer, SessionVerifiedOrder

MAX_FAILED_LOOKUPS = 5  # AGENTS.md §7

# One answer for "no such order" and "wrong email", byte for byte, so the reply can't be used
# to discover which order numbers exist.
ORDER_NOT_FOUND = (
    '{"error":"order_not_found","message":"No order matches this number and email. Ask the '
    'customer to check both; never say which one is wrong."}'
)
TOO_MANY_ATTEMPTS = (
    '{"error":"too_many_attempts","message":"Order lookups are blocked in this conversation '
    'for security. Offer human support (handoff_to_human)."}'
)
NO_SESSION = '{"error":"session_required"}'
ORDER_DATA_MISSING = (
    '{"error":"order_data_missing","message":"Ask the customer for the order number and the '
    'purchase email first. For a general rule question, use search_policies instead."}'
)
# In a bound conversation: for another customer's order, an unknown order or a wrong email
# alike, so it never tells whether the order exists.
OTHER_CUSTOMER = (
    '{"error":"other_customer","message":"This conversation is linked to the customer whose '
    "order was verified earlier, and no order of theirs matches this number and email. Kindly "
    "explain that each conversation serves one person: for someone else's order, suggest "
    "starting a new conversation; if the order is theirs, ask to check both. Never say whether "
    'this order exists."}'
)
ORDER_NOT_VERIFIED = (
    '{"error":"order_not_verified","message":"This case is about an order: first ask the '
    "customer for the order number and purchase email and look it up (get_order_status), then "
    'open the case with that order_id."}'
)


class FailedLookupStore(Protocol):
    """Failed identity checks per session and per client: `FailedLookups` (memory) or
    `core.sessions.SqlFailedLookups` (database, API)."""

    def locked(self, session: str, client: str | None = None) -> bool: ...

    def record_failure(self, session: str, client: str | None = None) -> None: ...


class VerifiedOrderStore(Protocol):
    """Orders verified per session and the customer the session is bound to:
    `VerifiedOrders` (memory) or `SqlVerifiedOrders` (DB)."""

    def record(self, session: str, order_id: int) -> None: ...

    def verified(self, session: str | None, order_id: int) -> bool: ...

    def customer(self, session: str) -> int | None:
        """The customer this session is bound to (None before the first verified order)."""
        ...

    def bind(self, session: str, customer_id: int) -> int:
        """Bind the session to this customer unless it is bound already; returns the
        session's customer (check-and-set, so two racing lookups can't bind two customers)."""
        ...


def session_id(config: RunnableConfig | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("thread_id")


def client_key(config: RunnableConfig | None) -> str | None:
    """Opaque key of the caller (the API's salted IP hash), from the run config like the
    session id — never a tool argument. None outside the API (CLI, evals)."""
    return ((config or {}).get("configurable") or {}).get("client_key")


class FailedLookups:
    """Counts failed identity checks per session and per client (in memory: CLI and tests;
    the API uses `core.sessions.SqlFailedLookups`). Shared by both order tools."""

    def __init__(self, limit: int = MAX_FAILED_LOOKUPS, client_limit: int | None = None) -> None:
        self.limit = limit
        self.client_limit = client_limit
        self._failures: Counter[str] = Counter()
        self._by_client: Counter[str] = Counter()
        self._lock = threading.Lock()

    def locked(self, session: str, client: str | None = None) -> bool:
        with self._lock:
            if self._failures[session] >= self.limit:
                return True
            limit = self.client_limit
            return client is not None and limit is not None and self._by_client[client] >= limit

    def record_failure(self, session: str, client: str | None = None) -> None:
        with self._lock:
            self._failures[session] += 1
            if client is not None:
                self._by_client[client] += 1


class SqlVerifiedOrders:
    """`VerifiedOrders` in the `session_verified_orders` table (API: shared by replicas,
    survives restarts). Sync: called from tools, which LangChain runs in a worker thread."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, session: str, order_id: int) -> None:
        with Session(self.engine) as db, db.begin():
            if db.get(SessionVerifiedOrder, (session, order_id)) is None:
                db.add(SessionVerifiedOrder(session_id=session, order_id=order_id))

    def verified(self, session: str | None, order_id: int) -> bool:
        if session is None:
            return False
        with Session(self.engine) as db:
            return db.get(SessionVerifiedOrder, (session, order_id)) is not None

    def customer(self, session: str) -> int | None:
        with Session(self.engine) as db:
            row = db.get(SessionCustomer, session)
            return row.customer_id if row else None

    def bind(self, session: str, customer_id: int) -> int:
        try:
            with Session(self.engine) as db, db.begin():
                if (row := db.get(SessionCustomer, session)) is not None:
                    return row.customer_id
                db.add(SessionCustomer(session_id=session, customer_id=customer_id))
            return customer_id
        except IntegrityError:  # another replica bound it first (primary key): that one wins
            bound = self.customer(session)
            assert bound is not None
            return bound


class VerifiedOrders:
    """Orders whose number + email were checked successfully, per session (in memory; the API
    keeps this in the database, step 17). Order-related handoffs need one."""

    def __init__(self) -> None:
        self._orders: dict[str, set[int]] = {}
        self._customers: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, session: str, order_id: int) -> None:
        with self._lock:
            self._orders.setdefault(session, set()).add(order_id)

    def verified(self, session: str | None, order_id: int) -> bool:
        with self._lock:
            return session is not None and order_id in self._orders.get(session, set())

    def customer(self, session: str) -> int | None:
        with self._lock:
            return self._customers.get(session)

    def bind(self, session: str, customer_id: int) -> int:
        with self._lock:
            return self._customers.setdefault(session, customer_id)
