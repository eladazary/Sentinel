"""Data-access helpers for market data. Thin functions over a SQLAlchemy
Session so they can be unit-tested against a real (or dockerised) database and
reused by both the API and the worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.models import DailyBar, LatestPrice, SentimentCache, SignalSnapshot

# Columns that get overwritten on an (symbol, ts) conflict during bar upsert.
_BAR_UPDATE_COLS = ("open", "high", "low", "close", "volume", "trade_count", "vwap")


def upsert_daily_bars(session: Session, rows: list[dict]) -> int:
    """Insert or update daily bars. Returns the number of rows submitted.

    Uses PostgreSQL ``ON CONFLICT`` so re-running a backfill is idempotent.
    """
    if not rows:
        return 0
    stmt = insert(DailyBar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyBar.symbol, DailyBar.ts],
        set_={c: getattr(stmt.excluded, c) for c in _BAR_UPDATE_COLS},
    )
    session.execute(stmt)
    return len(rows)


def count_daily_bars(session: Session, symbol: str | None = None) -> int:
    stmt = select(func.count()).select_from(DailyBar)
    if symbol is not None:
        stmt = stmt.where(DailyBar.symbol == symbol)
    return session.execute(stmt).scalar_one()


def get_recent_closes(
    session: Session, symbol: str, limit: int = 30
) -> list[tuple[datetime, float]]:
    """Return up to ``limit`` most recent (ts, close) pairs, oldest first."""
    stmt = (
        select(DailyBar.ts, DailyBar.close)
        .where(DailyBar.symbol == symbol)
        .order_by(DailyBar.ts.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [(ts, float(close)) for ts, close in reversed(rows)]


def get_last_close(session: Session, symbol: str) -> float | None:
    """Most recent daily close, used as the reference for day change."""
    stmt = (
        select(DailyBar.close)
        .where(DailyBar.symbol == symbol)
        .order_by(DailyBar.ts.desc())
        .limit(1)
    )
    val = session.execute(stmt).scalar_one_or_none()
    return float(val) if val is not None else None


def upsert_latest_price(
    session: Session,
    symbol: str,
    price: float,
    ts: datetime,
    updated_at: datetime,
    source: str = "alpaca",
) -> None:
    stmt = insert(LatestPrice).values(
        symbol=symbol, price=price, ts=ts, source=source, updated_at=updated_at
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[LatestPrice.symbol],
        set_={
            "price": stmt.excluded.price,
            "ts": stmt.excluded.ts,
            "source": stmt.excluded.source,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)


def get_latest_prices(session: Session, symbols: list[str]) -> dict[str, LatestPrice]:
    if not symbols:
        return {}
    stmt = select(LatestPrice).where(LatestPrice.symbol.in_(symbols))
    return {row.symbol: row for row in session.execute(stmt).scalars()}


def get_signal_snapshots(
    session: Session, symbols: list[str]
) -> dict[str, SignalSnapshot]:
    if not symbols:
        return {}
    stmt = select(SignalSnapshot).where(SignalSnapshot.symbol.in_(symbols))
    return {row.symbol: row for row in session.execute(stmt).scalars()}


def get_sentiment_cache(
    session: Session, symbols: list[str]
) -> dict[str, SentimentCache]:
    if not symbols:
        return {}
    stmt = select(SentimentCache).where(SentimentCache.symbol.in_(symbols))
    return {row.symbol: row for row in session.execute(stmt).scalars()}


@dataclass
class WatchlistRow:
    """Denormalised view of one watchlist ticker for the API."""

    symbol: str
    name: str
    price: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    as_of: datetime | None
    spark: list[float]
    conviction: float | None = None
    confidence: float | None = None
    technical_score: float | None = None
    news_score: float | None = None
    social_score: float | None = None
    crowding: bool = False
    signal: str | None = None
    drivers: list[str] = field(default_factory=list)


def build_watchlist_rows(
    session: Session,
    tickers: list[tuple[str, str]],
    spark_points: int = 30,
) -> list[WatchlistRow]:
    """Assemble the per-ticker watchlist payload from stored prices and bars."""
    symbols = [sym for sym, _ in tickers]
    latest = get_latest_prices(session, symbols)
    signals = get_signal_snapshots(session, symbols)
    sentiment = get_sentiment_cache(session, symbols)
    out: list[WatchlistRow] = []
    for symbol, name in tickers:
        lp = latest.get(symbol)
        price = float(lp.price) if lp else None
        as_of = lp.ts if lp else None
        prev_close = get_last_close(session, symbol)
        change = change_pct = None
        if price is not None and prev_close:
            change = price - prev_close
            change_pct = (change / prev_close) * 100.0
        spark = [c for _, c in get_recent_closes(session, symbol, spark_points)]
        sig = signals.get(symbol)
        sc = sentiment.get(symbol)
        out.append(
            WatchlistRow(
                symbol=symbol,
                name=name,
                price=price,
                prev_close=prev_close,
                change=change,
                change_pct=change_pct,
                as_of=as_of,
                spark=spark,
                conviction=sig.conviction if sig else None,
                confidence=sig.confidence if sig else None,
                technical_score=sig.technical_score if sig else None,
                news_score=sig.news_score if sig else None,
                social_score=sig.social_score if sig else None,
                crowding=bool(sc.social_crowding) if sc else False,
                signal=sig.signal if sig else None,
                drivers=list(sig.drivers) if sig and sig.drivers else [],
            )
        )
    return out


def recent_news(session: Session, limit: int = 50, symbol: str | None = None):
    from sentinel.models import NewsItemRow

    stmt = select(NewsItemRow).order_by(NewsItemRow.ts.desc()).limit(limit)
    if symbol:
        stmt = select(NewsItemRow).where(NewsItemRow.symbol == symbol).order_by(
            NewsItemRow.ts.desc()
        ).limit(limit)
    return list(session.execute(stmt).scalars())
