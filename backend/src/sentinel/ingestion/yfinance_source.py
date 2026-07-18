"""yfinance market-data source (free, no API keys).

Implements the same surface as ``AlpacaMarketData`` so it drops into
``backfill_prices``/``ingest_latest_prices``. Used for the historical backfill
that feeds the Phase 1 model and backtester; Alpaca remains the path for live
paper/live trading prices when credentials are present.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from sentinel.logging_config import get_logger

log = get_logger(__name__)


def _to_utc(ts: pd.Timestamp) -> datetime:
    """Normalize a pandas Timestamp to a tz-aware UTC datetime."""
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _frame_to_rows(symbol: str, df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for idx, r in df.iterrows():
        close = r["Close"]
        if pd.isna(close):
            continue  # skip non-trading rows (e.g. VIX gaps)
        rows.append(
            {
                "symbol": symbol,
                "ts": _to_utc(pd.Timestamp(idx)),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(close),
                "volume": int(r["Volume"]) if not pd.isna(r["Volume"]) else 0,
                "trade_count": None,
                "vwap": None,
            }
        )
    return rows


class YFinanceMarketData:
    """Historical + latest daily prices via yfinance (auto-adjusted)."""

    def get_daily_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for symbol in symbols:
            # auto_adjust=True -> split/dividend-adjusted OHLC (Alpaca ALL parity).
            df = yf.download(
                symbol,
                start=start.date(),
                end=end.date(),
                interval="1d",
                auto_adjust=True,
                progress=False,
                actions=False,
            )
            if df is None or df.empty:
                log.warning("yfinance returned no data for %s", symbol)
                out[symbol] = []
                continue
            # yfinance may return MultiIndex columns for a single symbol; flatten.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out[symbol] = _frame_to_rows(symbol, df)
        return out

    def get_latest_prices(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, datetime]]:
        """Most recent close per symbol (delayed; fine for the watchlist view)."""
        result: dict[str, tuple[float, datetime]] = {}
        if not symbols:
            return result
        df = yf.download(
            symbols,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            actions=False,
            group_by="ticker",
        )
        if df is None or df.empty:
            return result
        for symbol in symbols:
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    sub = df[symbol] if symbol in df.columns.get_level_values(0) else None
                else:
                    sub = df  # single symbol
                if sub is None:
                    continue
                closes = sub["Close"].dropna()
                if closes.empty:
                    continue
                result[symbol] = (float(closes.iloc[-1]), _to_utc(closes.index[-1]))
            except (KeyError, IndexError):
                continue
        return result
