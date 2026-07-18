"""Alpaca market-data client wrapper.

Wraps ``alpaca-py`` behind a small, mockable surface and keeps the bar → row
transform as a pure function so it can be tested without any network or SDK
objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

_FEED_MAP = {"iex": DataFeed.IEX, "sip": DataFeed.SIP}


class BarLike(Protocol):
    """Structural type for an Alpaca daily bar (the fields we consume)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: Any
    vwap: Any


def normalize_bar(symbol: str, bar: BarLike) -> dict:
    """Convert an Alpaca bar into a ``daily_bars`` row dict. Pure function."""
    trade_count = getattr(bar, "trade_count", None)
    vwap = getattr(bar, "vwap", None)
    return {
        "symbol": symbol,
        "ts": bar.timestamp,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
        "trade_count": int(trade_count) if trade_count is not None else None,
        "vwap": float(vwap) if vwap is not None else None,
    }


class AlpacaMarketData:
    """Historical + latest price access over Alpaca's market-data API."""

    def __init__(self, api_key: str, secret_key: str, feed: str = "iex") -> None:
        if not api_key or not secret_key:
            raise ValueError("Alpaca API key and secret are required")
        self._feed = _FEED_MAP[feed]
        self._client = StockHistoricalDataClient(api_key, secret_key)

    def get_daily_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict]]:
        """Fetch adjusted daily bars for ``symbols`` in ``[start, end]``.

        Returns a mapping of symbol → list of normalized row dicts (chronological).
        """
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment=Adjustment.ALL,  # split- and dividend-adjusted
            feed=self._feed,
        )
        barset = self._client.get_stock_bars(request)
        out: dict[str, list[dict]] = {}
        # barset.data is a dict[symbol, list[Bar]].
        for symbol, bars in barset.data.items():
            out[symbol] = [normalize_bar(symbol, b) for b in bars]
        return out

    def get_latest_prices(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, datetime]]:
        """Return the latest trade price and timestamp per symbol."""
        request = StockLatestTradeRequest(
            symbol_or_symbols=symbols, feed=self._feed
        )
        trades = self._client.get_stock_latest_trade(request)
        return {
            symbol: (float(trade.price), trade.timestamp)
            for symbol, trade in trades.items()
        }
