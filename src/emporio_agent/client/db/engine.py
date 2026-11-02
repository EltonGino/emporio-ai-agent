"""Database engines. SQLite by default; any SQLAlchemy URL works (e.g. Postgres).

SQLite connections get WAL journaling and a busy timeout (several users at once: readers
don't block the writer, and a writer waits instead of failing with "database is locked").
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from emporio_agent.config import get_settings

BUSY_TIMEOUT_MS = 5000
SQLITE_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}",
)
# Async drivers for the API's own queries (sessions, limits).
ASYNC_DRIVERS = {"sqlite": "sqlite+aiosqlite", "postgresql": "postgresql+asyncpg"}


def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()  # works for sqlite3 and the aiosqlite adapter
    for pragma in SQLITE_PRAGMAS:
        cursor.execute(pragma)
    cursor.close()


def _prepare_sqlite_file(url: str) -> bool:
    """True if the URL is SQLite (creating the file's directory)."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return False
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    return True


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if not _prepare_sqlite_file(url):
        return create_engine(url)
    engine = create_engine(url)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def make_async_engine(url: str | None = None) -> AsyncEngine:
    """The same database through an async driver (aiosqlite / asyncpg)."""
    url = url or get_settings().database_url
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    async_url = parsed.set(drivername=ASYNC_DRIVERS.get(backend, parsed.drivername))
    if not _prepare_sqlite_file(url):
        return create_async_engine(async_url)
    engine = create_async_engine(async_url)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine
