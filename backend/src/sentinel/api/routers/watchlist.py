"""Watchlist endpoint: the universe with live-ish prices and a mini sparkline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep, watchlist_dep
from sentinel.config import Settings, Watchlist
from sentinel.repositories import build_watchlist_rows
from sentinel.schemas import WatchlistResponse, WatchlistTicker
from sentinel.universe import (
    MAX_TICKERS,
    UniverseError,
    add_ticker,
    load_universe,
    remove_ticker,
)

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


@router.post("/watchlist/tickers", response_model=WatchlistResponse)
def add_watchlist_ticker(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    symbol: str = Body(embed=True),
    name: str = Body(default="", embed=True),
    sector_etf: str | None = Body(default=None, embed=True),
) -> WatchlistResponse:
    """Add a ticker. It trades once the worker has backfilled its history."""
    load_universe(db, settings)  # ensure the table is seeded before counting
    try:
        add_ticker(db, symbol, name, sector_etf)
    except UniverseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _render(db, settings)


@router.delete("/watchlist/tickers/{symbol}", response_model=WatchlistResponse)
def remove_watchlist_ticker(
    symbol: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> WatchlistResponse:
    load_universe(db, settings)
    try:
        if not remove_ticker(db, symbol):
            raise HTTPException(status_code=404, detail=f"{symbol} is not in the watchlist")
    except UniverseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _render(db, settings)


@router.get("/watchlist", response_model=WatchlistResponse)
def watchlist(
    db: Session = Depends(get_db),
    wl: Watchlist = Depends(watchlist_dep),
    settings: Settings = Depends(settings_dep),
) -> WatchlistResponse:
    return _render(db, settings, wl)


def _render(
    db: Session, settings: Settings, wl: Watchlist | None = None
) -> WatchlistResponse:
    wl = wl or load_universe(db, settings)
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
    return WatchlistResponse(
        mode=settings.mode, count=len(payload), tickers=payload, max_tickers=MAX_TICKERS
    )
