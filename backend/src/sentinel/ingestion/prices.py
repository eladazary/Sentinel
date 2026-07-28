"""Price ingestion orchestration: 5-year daily backfill + latest-price polling.

The functions here take a ``MarketData`` provider (the Alpaca wrapper in
production, a fake in tests) and a SQLAlchemy session factory, so the control
flow is testable independently of the network and of TimescaleDB.
"""

from __future__ import annotations

import time
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
    source: str = "unknown",
) -> int:
    """Fetch and store the latest price for each symbol. Returns count updated."""
    if not symbols:
        return 0
    observed_at = now or _utcnow()
    prices = md.get_latest_prices(symbols)
    updated = 0
    with session_factory() as session:
        for symbol, (price, ts) in prices.items():
            # Record the provider that actually served the quote. This used to
            # default to "alpaca" regardless, which made yfinance daily closes
            # look like live Alpaca trades.
            repo.upsert_latest_price(
                session, symbol, price, ts, updated_at=observed_at, source=source
            )
            updated += 1
    log.info("latest prices updated for %d/%d symbols", updated, len(symbols))
    return updated


def iter_batches(items: list, size: int) -> Iterator[list]:
    """Yield ``items`` in chunks of at most ``size`` (Alpaca multi-symbol cap)."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_market_data(settings) -> MarketData:
    """Pick a provider for *historical* backfill.

    Prefers yfinance (free, no keys, and no rate limit worth worrying about for
    years of daily bars). Falls back to Alpaca when explicitly configured.
    """
    from sentinel.ingestion.yfinance_source import YFinanceMarketData

    if settings.backfill_source == "alpaca":
        if not settings.has_alpaca_credentials:
            raise ValueError("backfill_source=alpaca but Alpaca credentials are unset")
        return _alpaca_market_data(settings)
    return YFinanceMarketData()


# Providers that return an actual last trade. Anything outside this set reports
# delayed or daily-close data and must not be used to price an entry.
REALTIME_QUOTE_SOURCES = frozenset({"alpaca"})

_QUOTE_PROBE_TTL_SECONDS = 300.0
_quote_probe: tuple[float, str | None] | None = None


def reset_quote_probe() -> None:
    """Forget the cached quote-feed verdict (tests, config changes)."""
    global _quote_probe
    _quote_probe = None


def _probe_quotes(md: MarketData, symbol: str) -> str | None:
    """Return None if the feed answers, else a short failure description."""
    global _quote_probe
    now = time.monotonic()
    if _quote_probe is not None and now - _quote_probe[0] < _QUOTE_PROBE_TTL_SECONDS:
        return _quote_probe[1]
    try:
        md.get_latest_prices([symbol])
        err = None
    except Exception as exc:  # noqa: BLE001 - any failure means "don't trust it"
        text = str(exc)
        if "401" in text or "unauthorized" in text.lower():
            err = "Alpaca rejected the API credentials (HTTP 401)"
        elif "403" in text or "forbidden" in text.lower():
            err = "Alpaca refused the request (HTTP 403)"
        else:
            err = text
    _quote_probe = (now, err)
    return err


def make_quote_source(settings) -> tuple[MarketData, str]:
    """Pick a provider for *live quotes*, and name it. Returns (provider, name).

    Deliberately separate from backfill. yfinance's ``get_latest_prices`` returns
    the most recent daily *close*, so it cannot price an order: the limit lands on
    yesterday's number and never fills. Alpaca returns the latest trade, and it is
    the venue the order goes to, so its quote is the right reference.

    The feed is probed before use. Credentials that are present but rejected used
    to fail on every batched call and write nothing at all, so every symbol showed
    up as "no quote" with no explanation anywhere near the cause.
    """
    resolved = settings.quote_source
    if resolved == "auto":
        resolved = "alpaca" if settings.has_alpaca_credentials else "yfinance"

    if resolved == "alpaca":
        if not settings.has_alpaca_credentials:
            raise ValueError("quote_source=alpaca but Alpaca credentials are unset")
        md = _alpaca_market_data(settings)
        err = _probe_quotes(md, settings.benchmark_symbol)
        if err is None:
            return md, "alpaca"
        log.error(
            "QUOTE FEED UNUSABLE — %s. Falling back to yfinance so the dashboard "
            "still has prices, but entries will be REFUSED: that feed reports "
            "daily closes, not live trades. Fix the credentials to trade.",
            err,
        )
    else:
        log.warning(
            "quotes are coming from yfinance, which reports the last daily CLOSE. "
            "Entries will be refused; configure Alpaca credentials to trade."
        )

    from sentinel.ingestion.yfinance_source import YFinanceMarketData

    return YFinanceMarketData(), "yfinance"


def _alpaca_market_data(settings):
    from sentinel.ingestion.alpaca import AlpacaMarketData

    return AlpacaMarketData(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        settings.alpaca_data_feed,
    )
