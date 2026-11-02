"""Session state kept in the database, never in process memory (several API replicas can share
it): last activity (retention), messages per session, messages per day (demo caps) and failed
identity checks (lockout). Client-agnostic.

Limits are enforced with conditional UPDATEs (`... SET n = n + 1 WHERE n < limit`), which are
atomic in SQLite and Postgres, so two replicas can't both let the 16th message through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String(100), primary_key=True),
    Column("created_at", DateTime, nullable=False),  # UTC
    Column("last_activity", DateTime, nullable=False, index=True),  # UTC, for retention
    Column("message_count", Integer, nullable=False, default=0),
    Column("failed_identity_checks", Integer, nullable=False, default=0),
)

daily_message_counts = Table(
    "daily_message_counts",
    metadata,
    Column("day", Date, primary_key=True),  # store-local calendar day
    Column("message_count", Integer, nullable=False, default=0),
)


def create_session_tables(engine: Engine) -> None:
    metadata.create_all(engine)


async def _insert_if_missing(conn, table: Table, where, row: dict) -> None:
    if (await conn.execute(select(table).where(where))).first() is not None:
        return
    try:
        async with conn.begin_nested():  # a SAVEPOINT: a concurrent insert doesn't abort us
            await conn.execute(insert(table).values(**row))
    except IntegrityError:
        pass  # another request created it first


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: Literal["session_limit", "daily_cap"] | None = None


def utcnow() -> datetime:
    """Naive UTC (SQLite has no time zones; Postgres TIMESTAMP WITHOUT TIME ZONE likewise)."""
    return datetime.now(UTC).replace(tzinfo=None)


class _Rejected(Exception):
    """Rolls back the admission transaction (nothing is counted)."""


class AsyncSessionStore:
    """The API's session bookkeeping, through an async engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def admit(
        self, session_id: str, now: datetime, day: date, max_per_session: int, daily_cap: int
    ) -> Admission:
        """Count one customer message if both limits allow it; otherwise count nothing."""
        try:
            async with self.engine.begin() as conn:
                await _insert_if_missing(
                    conn,
                    sessions,
                    sessions.c.session_id == session_id,
                    {"session_id": session_id, "created_at": now, "last_activity": now},
                )
                await _insert_if_missing(
                    conn, daily_message_counts, daily_message_counts.c.day == day, {"day": day}
                )
                counted = await conn.execute(
                    update(sessions)
                    .where(
                        sessions.c.session_id == session_id,
                        sessions.c.message_count < max_per_session,
                    )
                    .values(message_count=sessions.c.message_count + 1, last_activity=now)
                )
                if counted.rowcount == 0:
                    raise _Rejected("session_limit")
                counted = await conn.execute(
                    update(daily_message_counts)
                    .where(
                        daily_message_counts.c.day == day,
                        daily_message_counts.c.message_count < daily_cap,
                    )
                    .values(message_count=daily_message_counts.c.message_count + 1)
                )
                if counted.rowcount == 0:
                    raise _Rejected("daily_cap")
        except _Rejected as rejected:
            return Admission(False, rejected.args[0])
        return Admission(True)

    async def expired(self, before: datetime) -> list[str]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sessions.c.session_id).where(sessions.c.last_activity < before)
            )
            return [row.session_id for row in rows]

    async def delete(self, session_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(delete(sessions).where(sessions.c.session_id == session_id))


class SqlFailedLookups:
    """Failed identity checks per session in the `sessions` table (survives restarts and is
    shared by replicas). Same interface as the in-memory `client.identity.FailedLookups`.
    Sync: it is called from tools, which LangChain runs in a worker thread."""

    def __init__(self, engine: Engine, limit: int) -> None:
        self.engine = engine
        self.limit = limit

    def locked(self, session: str) -> bool:
        with self.engine.connect() as conn:
            failures = conn.execute(
                select(sessions.c.failed_identity_checks).where(sessions.c.session_id == session)
            ).scalar()
        return (failures or 0) >= self.limit

    def record_failure(self, session: str) -> None:
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(sessions)
                .where(sessions.c.session_id == session)
                .values(failed_identity_checks=sessions.c.failed_identity_checks + 1)
            )
            if changed.rowcount == 0:  # a session the API didn't register (e.g. direct use)
                now = utcnow()
                conn.execute(
                    insert(sessions).values(
                        session_id=session,
                        created_at=now,
                        last_activity=now,
                        message_count=0,
                        failed_identity_checks=1,
                    )
                )
