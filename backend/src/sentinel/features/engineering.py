"""Assemble the technical feature matrix for one symbol.

Combines the symbol's own OHLCV-derived indicators with market-context features
(relative strength vs the benchmark and its sector ETF, plus a VIX regime
percentile). Every feature at row t is computable from data available at t — no
lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.features import indicators as ind

# The exact, ordered set of model input columns. Training and inference both
# read this so the feature contract can't drift.
FEATURE_COLUMNS: list[str] = [
    "dist_ma20",
    "dist_ma50",
    "dist_ma200",
    "ma20_over_ma50",
    "ma50_over_ma200",
    "rsi14",
    "macd_hist_norm",
    "bb_pctb",
    "atr_pct",
    "vol_z",
    "gap",
    "ret5",
    "ret10",
    "ret20",
    "rs_spy_20",
    "rs_spy_60",
    "rs_sector_20",
    "vix_level",
    "vix_pctile",
]


def _rel_strength(close: pd.Series, bench: pd.Series, window: int) -> pd.Series:
    """Symbol return minus benchmark return over ``window`` bars."""
    return close.pct_change(window) - bench.pct_change(window)


def build_features(
    bars: pd.DataFrame,
    spy_close: pd.Series,
    vix_close: pd.Series,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Return a feature DataFrame indexed like ``bars``.

    ``bars`` must have columns open/high/low/close/volume and a sorted
    DatetimeIndex. ``spy_close``/``vix_close``/``sector_close`` are close-price
    Series that get aligned to ``bars.index``.
    """
    df = bars.sort_index()
    close, high, low, vol, open_ = (
        df["close"],
        df["high"],
        df["low"],
        df["volume"],
        df["open"],
    )

    spy = spy_close.reindex(df.index)
    vix = vix_close.reindex(df.index)
    sector = sector_close.reindex(df.index) if sector_close is not None else None

    ma20, ma50, ma200 = ind.sma(close, 20), ind.sma(close, 50), ind.sma(close, 200)

    feats = pd.DataFrame(index=df.index)
    feats["dist_ma20"] = close / ma20 - 1.0
    feats["dist_ma50"] = close / ma50 - 1.0
    feats["dist_ma200"] = close / ma200 - 1.0
    feats["ma20_over_ma50"] = ma20 / ma50 - 1.0
    feats["ma50_over_ma200"] = ma50 / ma200 - 1.0
    feats["rsi14"] = ind.rsi(close, 14)
    feats["macd_hist_norm"] = ind.macd_histogram(close) / close
    feats["bb_pctb"] = ind.bollinger_pctb(close, 20, 2.0)
    feats["atr_pct"] = ind.atr(high, low, close, 14) / close
    feats["vol_z"] = ind.volume_zscore(vol, 30)
    feats["gap"] = ind.overnight_gap(open_, close)
    feats["ret5"] = close.pct_change(5)
    feats["ret10"] = close.pct_change(10)
    feats["ret20"] = close.pct_change(20)
    feats["rs_spy_20"] = _rel_strength(close, spy, 20)
    feats["rs_spy_60"] = _rel_strength(close, spy, 60)
    feats["rs_sector_20"] = (
        _rel_strength(close, sector, 20)
        if sector is not None
        else pd.Series(np.nan, index=df.index)
    )
    feats["vix_level"] = vix
    feats["vix_pctile"] = ind.rolling_percentile(vix, 252)

    return feats[FEATURE_COLUMNS]
