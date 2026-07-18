"""Persistence for signals, the immutable decision log, and equity snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.models import Decision, EquitySnapshot, SignalSnapshot


def log_decision(
    session: Session,
    *,
    ts: datetime,
    symbol: str,
    action: str,
    signal: str,
    conviction: float,
    confidence: float,
    risk_factor: int,
    mode: str,
    reason: str,
    drivers: list[str],
    features: dict | None = None,
    sizing: dict | None = None,
    broker_order_id: str | None = None,
) -> None:
    """Append one immutable decision record (fired or skipped)."""
    session.add(
        Decision(
            ts=ts,
            symbol=symbol,
            action=action,
            signal=signal,
            conviction=conviction,
            confidence=confidence,
            risk_factor=risk_factor,
            mode=mode,
            reason=reason,
            drivers=drivers,
            features=features,
            sizing=sizing,
            broker_order_id=broker_order_id,
        )
    )


def upsert_signal_snapshot(
    session: Session,
    *,
    symbol: str,
    ts: datetime,
    conviction: float,
    confidence: float,
    technical_score: float | None,
    signal: str,
    drivers: list[str],
    news_score: float | None = None,
    social_score: float | None = None,
    model_version: str | None = None,
) -> None:
    stmt = insert(SignalSnapshot).values(
        symbol=symbol,
        ts=ts,
        conviction=conviction,
        confidence=confidence,
        technical_score=technical_score,
        news_score=news_score,
        social_score=social_score,
        signal=signal,
        drivers=drivers,
        model_version=model_version,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SignalSnapshot.symbol],
        set_={
            "ts": stmt.excluded.ts,
            "conviction": stmt.excluded.conviction,
            "confidence": stmt.excluded.confidence,
            "technical_score": stmt.excluded.technical_score,
            "news_score": stmt.excluded.news_score,
            "social_score": stmt.excluded.social_score,
            "signal": stmt.excluded.signal,
            "drivers": stmt.excluded.drivers,
            "model_version": stmt.excluded.model_version,
        },
    )
    session.execute(stmt)


def record_equity(
    session: Session,
    *,
    ts: datetime,
    equity: float,
    cash: float,
    exposure_pct: float,
    mode: str,
) -> None:
    stmt = insert(EquitySnapshot).values(
        ts=ts, equity=equity, cash=cash, exposure_pct=exposure_pct, mode=mode
    )
    # One snapshot per timestamp; ignore duplicates within the same cycle.
    stmt = stmt.on_conflict_do_nothing(index_elements=[EquitySnapshot.ts])
    session.execute(stmt)


def get_latest_signals(session: Session) -> dict[str, SignalSnapshot]:
    rows = session.execute(select(SignalSnapshot)).scalars()
    return {r.symbol: r for r in rows}


def recent_decisions(session: Session, limit: int = 50) -> list[Decision]:
    stmt = select(Decision).order_by(Decision.ts.desc(), Decision.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())
