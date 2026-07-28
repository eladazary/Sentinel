"""Signals and the decision log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, watchlist_dep
from sentinel.config import Watchlist
from sentinel.execution.decision_log import (
    decision_action_counts,
    get_latest_signals,
    recent_decisions,
)
from sentinel.schemas import DecisionOut, SignalOut

router = APIRouter(tags=["signals"])


@router.get("/signals", response_model=list[SignalOut])
def signals(
    db: Session = Depends(get_db), wl: Watchlist = Depends(watchlist_dep)
) -> list[SignalOut]:
    snaps = get_latest_signals(db)
    out: list[SignalOut] = []
    for symbol in wl.symbols:  # preserve watchlist order
        s = snaps.get(symbol)
        if s is None:
            continue
        out.append(
            SignalOut(
                symbol=s.symbol,
                ts=s.ts,
                conviction=s.conviction,
                confidence=s.confidence,
                technical_score=s.technical_score,
                signal=s.signal,
                drivers=list(s.drivers or []),
                model_version=s.model_version,
            )
        )
    return out


@router.get("/decisions/counts")
def decision_counts(db: Session = Depends(get_db)) -> dict[str, int]:
    """Row count per action, so the UI can label its filters."""
    return decision_action_counts(db)


@router.get("/decisions", response_model=list[DecisionOut])
def decisions(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    actions: str | None = Query(
        default=None, description="comma-separated, e.g. OPEN,EXIT,BREAKER"
    ),
    symbol: str | None = Query(default=None),
    exclude_skips: bool = Query(
        default=False, description="hide the per-cycle SKIP noise"
    ),
) -> list[DecisionOut]:
    rows = recent_decisions(
        db,
        limit=limit,
        actions=actions.split(",") if actions else None,
        symbol=symbol,
        exclude_skips=exclude_skips,
    )
    return [
        DecisionOut(
            id=d.id,
            ts=d.ts,
            symbol=d.symbol,
            action=d.action,
            signal=d.signal,
            conviction=d.conviction,
            confidence=d.confidence,
            risk_factor=d.risk_factor,
            mode=d.mode,
            reason=d.reason,
            drivers=list(d.drivers or []),
            broker_order_id=d.broker_order_id,
        )
        for d in rows
    ]
