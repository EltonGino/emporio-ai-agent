"""Injectable clock. Every "now" in the app comes from here, always in the store timezone.

`AGENT_NOW` (or the CLI's `--now`) freezes time so demos, recorded conversations and tests
are reproducible: deadlines, delays and open/closed status depend on the date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from emporio_agent.config import get_client_config, get_settings


class Clock(Protocol):
    def now(self) -> datetime: ...


def to_store_time(moment: datetime, tz: ZoneInfo) -> datetime:
    """Naive datetimes are read as store time; aware ones are converted to it."""
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


@dataclass(frozen=True)
class SystemClock:
    tz: ZoneInfo

    def now(self) -> datetime:
        return datetime.now(self.tz)


@dataclass(frozen=True)
class FixedClock:
    """Always returns the same moment (simulated "now")."""

    at: datetime

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")

    def now(self) -> datetime:
        return self.at


def get_clock(now: datetime | None = None) -> Clock:
    """Fixed clock at `now`, else at AGENT_NOW, else real time — all in store time."""
    tz = get_client_config().store.tz
    simulated = now or get_settings().agent_now
    if simulated is None:
        return SystemClock(tz)
    return FixedClock(to_store_time(simulated, tz))
