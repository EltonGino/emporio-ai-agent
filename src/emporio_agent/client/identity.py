"""Identity check for order lookups: order number + purchase email, with a per-session lockout.

The session id comes from the tool's run config (`configurable.thread_id`, set by the agent's
caller), never from a tool argument, so the model can't reset or choose the counter.
"""

from __future__ import annotations

import threading
from collections import Counter

from langchain_core.runnables import RunnableConfig

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
ORDER_NOT_VERIFIED = (
    '{"error":"order_not_verified","message":"This case is about an order: first ask the '
    "customer for the order number and purchase email and look it up (get_order_status), then "
    'open the case with that order_id."}'
)


def session_id(config: RunnableConfig | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("thread_id")


class FailedLookups:
    """Counts failed identity checks per session (in memory; shared by both order tools)."""

    def __init__(self, limit: int = MAX_FAILED_LOOKUPS) -> None:
        self.limit = limit
        self._failures: Counter[str] = Counter()
        self._lock = threading.Lock()

    def locked(self, session: str) -> bool:
        with self._lock:
            return self._failures[session] >= self.limit

    def record_failure(self, session: str) -> None:
        with self._lock:
            self._failures[session] += 1


class VerifiedOrders:
    """Orders whose number + email were checked successfully, per session (in memory; the API
    keeps this in the database, step 17). Order-related handoffs need one."""

    def __init__(self) -> None:
        self._orders: dict[str, set[int]] = {}
        self._lock = threading.Lock()

    def record(self, session: str, order_id: int) -> None:
        with self._lock:
            self._orders.setdefault(session, set()).add(order_id)

    def verified(self, session: str | None, order_id: int) -> bool:
        with self._lock:
            return session is not None and order_id in self._orders.get(session, set())
