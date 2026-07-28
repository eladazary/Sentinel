"""Operational system state: mode, the dry-run clock, breaker events, cool-off.

Backed by the single-row ``system_state`` table plus ``breaker_events``. This is
the durable record the go-live gate and the LIVE-unlock guardrails read from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from sentinel.models import BreakerEvent, EquitySnapshot, SystemState


def get_state(session: Session) -> SystemState:
    """Return the single state row, creating it (and starting the dry run) once."""
    state = session.get(SystemState, 1)
    if state is None:
        now = datetime.now(timezone.utc)
        state = SystemState(
            id=1, mode="DRY_RUN", dry_run_started_at=now, updated_at=now
        )
        session.add(state)
        session.flush()
    return state


def ensure_dry_run_started(session: Session, now: datetime | None = None) -> datetime:
    state = get_state(session)
    if state.dry_run_started_at is None:
        state.dry_run_started_at = now or datetime.now(timezone.utc)
        state.updated_at = now or datetime.now(timezone.utc)
    return state.dry_run_started_at


def record_breaker_event(
    session: Session,
    *,
    ts: datetime,
    kind: str,
    detail: str,
    day_pnl_pct: float | None = None,
    drawdown_pct: float | None = None,
) -> None:
    session.add(
        BreakerEvent(
            ts=ts, kind=kind, detail=detail,
            day_pnl_pct=day_pnl_pct, drawdown_pct=drawdown_pct,
        )
    )
    state = get_state(session)
    state.last_breaker_at = ts
    state.updated_at = ts


MARKET_TZ = "America/New_York"


def trading_days_count(session: Session) -> int:
    """Distinct *trading* days with an equity snapshot.

    Two corrections over a plain count of calendar dates, both of which inflated
    the go-live gate's "≥ N trading days on paper" criterion:

    1. Weekends are excluded. The worker records equity every cycle, so any
       weekend it happened to be running used to count as a day of paper
       trading — days on which no order could possibly have been placed.
    2. The day is bucketed in market time, not UTC. A snapshot at 00:30 UTC
       Saturday is 20:30 Friday in New York, i.e. a genuine trading day; naive
       UTC bucketing both drops it and invents a Saturday.

    The two sources carry different kinds of timestamp, so each is bucketed on
    its own terms:

    * ``replay`` rows are *date markers* taken from the daily-bar index and
      stamped at midnight UTC. They are trading days by construction. Converting
      them to market time would shift each back to the previous evening and drop
      every Monday.
    * ``live`` rows are real instants, so they are bucketed in market time and
      filtered to weekdays.

    Market holidays are still counted if a live snapshot exists for one. Since
    the loop only runs the cycle while ``is_market_open``, that can only happen
    for rows written before this change.
    """
    et = func.timezone(MARKET_TZ, EquitySnapshot.ts)
    is_replay = EquitySnapshot.source == "replay"
    day = case((is_replay, func.date(EquitySnapshot.ts)), else_=func.date(et))
    counts = case(
        (is_replay, True),
        # Postgres dow: 0 = Sunday … 6 = Saturday.
        else_=func.extract("dow", et).between(1, 5),
    )
    return session.execute(
        select(func.count(func.distinct(day))).where(counts)
    ).scalar_one()


def breaker_event_count(session: Session, unacknowledged_only: bool = False) -> int:
    stmt = select(func.count()).select_from(BreakerEvent)
    if unacknowledged_only:
        stmt = stmt.where(BreakerEvent.acknowledged.is_(False))
    return session.execute(stmt).scalar_one()


def in_cooloff(session: Session, hours: int, now: datetime | None = None) -> bool:
    """True if a breaker fired within the last ``hours`` (blocks LIVE unlock)."""
    state = get_state(session)
    if state.last_breaker_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - state.last_breaker_at) < timedelta(hours=hours)
