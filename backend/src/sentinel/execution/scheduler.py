"""Market-hours scheduling helpers (spec §7).

Signals recompute every 15 minutes during market hours; new entries are only
allowed in the 10:00–15:30 ET window (skip the open/close chaos).

NOTE: US market holidays are not yet handled here — only weekends and RTH. The
broker will still reject/queue off-session orders, but a holiday calendar is a
follow-up (flagged for Phase 2 hardening).
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
ENTRY_WINDOW_OPEN = time(10, 0)
ENTRY_WINDOW_CLOSE = time(15, 30)

RECOMPUTE_INTERVAL_SECONDS = 15 * 60


def _et(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    return now.astimezone(ET)


def is_weekday(now: datetime) -> bool:
    return _et(now).weekday() < 5  # Mon–Fri


def is_market_open(now: datetime) -> bool:
    n = _et(now)
    return is_weekday(n) and REGULAR_OPEN <= n.time() < REGULAR_CLOSE


def in_entry_window(now: datetime) -> bool:
    n = _et(now)
    return is_weekday(n) and ENTRY_WINDOW_OPEN <= n.time() <= ENTRY_WINDOW_CLOSE
