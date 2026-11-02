"""Forget sessions idle for more than SESSION_RETENTION_DAYS (the API also does this at start).

Usage: uv run python scripts/cleanup_sessions.py
Deletes their conversation checkpoints and traces, anonymizes their handoffs and drops their
session rows — the same as DELETE /api/sessions/{id}.
"""

import asyncio

from emporio_agent.api.main import cleanup_expired_sessions, open_runtime
from emporio_agent.config import TRACES_DIR, get_client_config, get_settings


async def main() -> None:
    async with open_runtime(get_settings(), get_client_config(), TRACES_DIR) as state:
        removed = await cleanup_expired_sessions(state)
    print(f"{removed} expired session(s) removed")


if __name__ == "__main__":
    asyncio.run(main())
