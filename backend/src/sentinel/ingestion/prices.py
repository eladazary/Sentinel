"""Price ingestion orchestration: 5-year daily backfill + latest-price polling.

The functions here take a ``MarketData`` provider (the Alpaca wrapper in
production, a fake in tests) and a SQLAlchemy session factory, so the control
flow is testable independently of the network and of TimescaleDB.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from sentinel import repositories as repo
from sentinel.config import Watchlist
from sentinel.db import session_scope
from sentinel.logging_config import get_logger

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


class MarketData(Protocol):
    def get_daily_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict]]: ...

    def get_latest_prices(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, datetime]]: ...


def backfill_window(now: datetime, years: int) -> tuple[datetime, datetime]:
    """Compute the [start, end] window for a ``years``-long daily backfill."""
    end = now
    start = end - timedelta(days=365 * years)
    return start, end


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backfill_symbols(
    md: MarketData,
    symbols: list[str],
    years: int = 5,
    *,
    now: datetime | None = None,
    session_factory: SessionFactory = session_scope,
    force: bool = False,
) -> dict[str, int]:
    """Backfill daily bars for an explicit list of symbols.

    Skips symbols that already have data unless ``force`` is set. Returns a
    mapping of symbol → number of bars written.
    """
    start, end = backfill_window(now or _utcnow(), years)
    results: dict[str, int] = {}

    for symbol in symbols:
        with session_factory() as session:
            existing = repo.count_daily_bars(session, symbol)
            if existing and not force:
                log.info(
                    "backfill: %s already has %d bars, skipping", symbol, existing
                )
                results[symbol] = 0
                continue

        log.info("backfill: fetching %s daily bars %s → %s", symbol, start, end)
        bars_by_symbol = md.get_daily_bars([symbol], start, end)
        rows = bars_by_symbol.get(symbol, [])
        with session_factory() as session:
            written = repo.upsert_daily_bars(session, rows)
        results[symbol] = written
        log.info("backfill: wrote %d bars for %s", written, symbol)

    total = sum(results.values())
    log.info("backfill complete: %d bars across %d symbols", total, len(results))
    return results


def backfill_prices(
    md: MarketData,
    watchlist: Watchlist,
    years: int = 5,
    *,
    now: datetime | None = None,
    session_factory: SessionFactory = session_scope,
    force: bool = False,
) -> dict[str, int]:
    """Backfill daily bars for every watchlist symbol (convenience wrapper)."""
    return backfill_symbols(
        md,
        watchlist.symbols,
        years,
        now=now,
        session_factory=session_factory,
        force=force,
    )


def ingest_latest_prices(
    md: MarketData,
    symbols: list[str],
    *,
    now: datetime | None = None,
    session_factory: SessionFactory = session_scope,
) -> int:
    """Fetch and store the latest price for each symbol. Returns count updated."""
    if not symbols:
        return 0
    observed_at = now or _utcnow()
    prices = md.get_latest_prices(symbols)
    updated = 0
    with session_factory() as session:
        for symbol, (price, ts) in prices.items():
            repo.upsert_latest_price(
                session, symbol, price, ts, updated_at=observed_at
            )
            updated += 1
    log.info("latest prices updated for %d/%d symbols", updated, len(symbols))
    return updated


def iter_batches(items: list, size: int) -> Iterator[list]:
    """Yield ``items`` in chunks of at most ``size`` (Alpaca multi-symbol cap)."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_market_data(settings) -> MarketData:
    """Pick a market-data provider.

    Prefers yfinance for backfill (free, no keys). Falls back to Alpaca when
    explicitly configured with credentials.
    """
    from sentinel.ingestion.yfinance_source import YFinanceMarketData

    if settings.backfill_source == "alpaca":
        if not settings.has_alpaca_credentials:
            raise ValueError("backfill_source=alpaca but Alpaca credentials are unset")
        from sentinel.ingestion.alpaca import AlpacaMarketData

        return AlpacaMarketData(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            settings.alpaca_data_feed,
        )
    return YFinanceMarketData()
