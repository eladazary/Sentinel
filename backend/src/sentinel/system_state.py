"""Operational system state: mode, the dry-run clock, breaker events, cool-off.

Backed by the single-row ``system_state`` table plus ``breaker_events``. This is
the durable record the go-live gate and the LIVE-unlock guardrails read from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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


def trading_days_count(session: Session) -> int:
    """Distinct calendar days with an equity snapshot (a proxy for dry-run days)."""
    return session.execute(
        select(func.count(func.distinct(func.date(EquitySnapshot.ts))))
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
