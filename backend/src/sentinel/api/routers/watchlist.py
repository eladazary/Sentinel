"""Watchlist endpoint: the universe with live-ish prices and a mini sparkline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep, watchlist_dep
from sentinel.config import Settings, Watchlist
from sentinel.repositories import build_watchlist_rows
from sentinel.schemas import WatchlistResponse, WatchlistTicker

router = APIRouter(tags=["market"])

# A latest price older than this is flagged stale in the UI (markets close;
# this is a "the worker hasn't updated recently" signal, not a hard error).
_STALE_AFTER = timedelta(minutes=30)


def _is_stale(as_of: datetime | None, now: datetime) -> bool:
    if as_of is None:
        return True
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return (now - as_of) > _STALE_AFTER


@router.get("/watchlist", response_model=WatchlistResponse)
def watchlist(
    db: Session = Depends(get_db),
    wl: Watchlist = Depends(watchlist_dep),
    settings: Settings = Depends(settings_dep),
) -> WatchlistResponse:
    now = datetime.now(timezone.utc)
    tickers = [(t.symbol, t.name) for t in wl.tickers]
    rows = build_watchlist_rows(db, tickers)
    payload = [
        WatchlistTicker(
            symbol=r.symbol,
            name=r.name,
            price=r.price,
            prev_close=r.prev_close,
            change=r.change,
            change_pct=r.change_pct,
            as_of=r.as_of,
            stale=_is_stale(r.as_of, now),
            spark=r.spark,
            conviction=r.conviction,
            confidence=r.confidence,
            technical_score=r.technical_score,
            news_score=r.news_score,
            social_score=r.social_score,
            crowding=r.crowding,
            signal=r.signal,
            drivers=r.drivers,
        )
        for r in rows
    ]
    return WatchlistResponse(mode=settings.mode, count=len(payload), tickers=payload)
