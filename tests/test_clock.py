from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from emporio_agent import clock as clock_module
from emporio_agent.clock import FixedClock, SystemClock, get_clock, to_store_time
from emporio_agent.config import Settings

STORE_TZ = ZoneInfo("America/Campo_Grande")


def test_naive_datetime_is_read_as_store_time():
    moment = to_store_time(datetime(2026, 3, 25, 10, 30), STORE_TZ)
    assert moment.isoformat() == "2026-03-25T10:30:00-04:00"


def test_aware_datetime_is_converted_to_store_time():
    moment = to_store_time(datetime(2026, 3, 25, 14, 30, tzinfo=UTC), STORE_TZ)
    assert moment.isoformat() == "2026-03-25T10:30:00-04:00"


def test_fixed_clock_requires_timezone():
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 3, 25, 10, 30))


def test_system_clock_is_in_store_timezone():
    now = SystemClock(STORE_TZ).now()
    assert now.utcoffset() == timedelta(hours=-4)
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_get_clock_uses_agent_now(monkeypatch):
    settings = Settings(agent_now=datetime(2026, 3, 25, 10, 30))
    monkeypatch.setattr(clock_module, "get_settings", lambda: settings)
    assert get_clock().now().isoformat() == "2026-03-25T10:30:00-04:00"


def test_explicit_now_overrides_agent_now(monkeypatch):
    settings = Settings(agent_now=datetime(2026, 3, 25, 10, 30))
    monkeypatch.setattr(clock_module, "get_settings", lambda: settings)
    assert get_clock(datetime(2026, 2, 20, 9, 0)).now().date().isoformat() == "2026-02-20"


def test_get_clock_without_simulation_is_real_time(monkeypatch):
    monkeypatch.setattr(clock_module, "get_settings", lambda: Settings(agent_now=None))
    assert isinstance(get_clock(), SystemClock)
