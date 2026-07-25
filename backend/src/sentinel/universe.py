"""The tradeable universe, stored in the DB so it can change at runtime.

config/watchlist.yaml seeds this table once and is ignored thereafter — it ships
inside the image, so edits made through the API would otherwise be lost on the
next rebuild. Everything that needs the universe should call ``load_universe``
rather than ``Settings.load_watchlist``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sentinel.config import Settings, Ticker, Watchlist
from sentinel.logging_config import get_logger
from sentinel.models import WatchlistTicker

log = get_logger(__name__)

MAX_TICKERS = 10  # spec §2 bounds the universe at 10 names


class UniverseError(ValueError):
    """A rejected edit (duplicate, unknown symbol, or the universe is full)."""


def load_universe(session: Session, settings: Settings) -> Watchlist:
    """The current universe, seeding from YAML on first use."""
    rows = _rows(session)
    if not rows:
        rows = _seed(session, settings)
    return Watchlist(
        tickers=[
            Ticker(symbol=r.symbol, name=r.name, sector_etf=r.sector_etf) for r in rows
        ]
    )


def add_ticker(
    session: Session, symbol: str, name: str, sector_etf: str | None = None
) -> WatchlistTicker:
    symbol = symbol.strip().upper()
    if not symbol:
        raise UniverseError("symbol is required")
    rows = _rows(session)
    if any(r.symbol == symbol for r in rows):
        raise UniverseError(f"{symbol} is already in the watchlist")
    if len(rows) >= MAX_TICKERS:
        raise UniverseError(
            f"the watchlist is full at {MAX_TICKERS} tickers — remove one first"
        )
    row = WatchlistTicker(
        symbol=symbol,
        name=(name or symbol).strip(),
        sector_etf=(sector_etf or None),
        added_at=datetime.now(timezone.utc),
        backfilled=False,
    )
    session.add(row)
    session.flush()
    log.info("added %s to the watchlist", symbol)
    return row


def remove_ticker(session: Session, symbol: str) -> bool:
    symbol = symbol.strip().upper()
    rows = _rows(session)
    if not any(r.symbol == symbol for r in rows):
        return False
    if len(rows) <= 1:
        raise UniverseError("the watchlist can't be empty")
    session.execute(delete(WatchlistTicker).where(WatchlistTicker.symbol == symbol))
    log.info("removed %s from the watchlist", symbol)
    return True


def pending_backfill(session: Session) -> list[WatchlistTicker]:
    """Tickers added since the last backfill sweep."""
    return list(
        session.execute(
            select(WatchlistTicker).where(WatchlistTicker.backfilled.is_(False))
        ).scalars()
    )


def mark_backfilled(session: Session, symbol: str) -> None:
    row = session.get(WatchlistTicker, symbol)
    if row is not None:
        row.backfilled = True


def _rows(session: Session) -> list[WatchlistTicker]:
    return list(
        session.execute(
            select(WatchlistTicker).order_by(WatchlistTicker.added_at.asc())
        ).scalars()
    )


def _seed(session: Session, settings: Settings) -> list[WatchlistTicker]:
    """One-time seed from the YAML that used to be the source of truth."""
    wl = settings.load_watchlist()
    now = datetime.now(timezone.utc)
    for i, t in enumerate(wl.tickers):
        session.add(WatchlistTicker(
            symbol=t.symbol, name=t.name, sector_etf=t.sector_etf,
            # Preserve file order, since added_at drives display order.
            added_at=now.replace(microsecond=i),
            # Seeded names are already backfilled by the worker's boot sweep.
            backfilled=True,
        ))
    session.flush()
    # Commit here: seeding happens inside read-only request paths that would
    # otherwise discard it, and it must only ever happen once.
    session.commit()
    log.info("seeded the watchlist from %s", settings.watchlist_path)
    return _rows(session)
