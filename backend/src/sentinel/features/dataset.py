"""Build model-ready datasets from stored daily bars.

Turns the ``daily_bars`` table into per-symbol OHLCV frames, feature matrices,
and a pooled, labelled training set across the whole watchlist.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.config import Watchlist
from sentinel.features.engineering import FEATURE_COLUMNS, build_features
from sentinel.features.labels import binary_label
from sentinel.models import DailyBar


def load_bars(session: Session, symbol: str) -> pd.DataFrame:
    """Load all daily bars for a symbol as a DatetimeIndexed OHLCV frame."""
    stmt = (
        select(
            DailyBar.ts,
            DailyBar.open,
            DailyBar.high,
            DailyBar.low,
            DailyBar.close,
            DailyBar.volume,
        )
        .where(DailyBar.symbol == symbol)
        .order_by(DailyBar.ts.asc())
    )
    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index(pd.DatetimeIndex(df["ts"])).drop(columns=["ts"])
    return df.astype(float)


def close_series(session: Session, symbol: str) -> pd.Series:
    return load_bars(session, symbol)["close"]


def build_symbol_features(
    session: Session, watchlist: Watchlist, symbol: str, settings
) -> pd.DataFrame:
    """Feature matrix for one symbol using stored context symbols."""
    bars = load_bars(session, symbol)
    if bars.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    spy = close_series(session, settings.benchmark_symbol)
    vix = close_series(session, settings.vix_symbol)
    sector_sym = watchlist.sector_for(symbol)
    sector = close_series(session, sector_sym) if sector_sym else None
    return build_features(bars, spy, vix, sector)


def build_training_frame(
    session: Session, watchlist: Watchlist, settings
) -> pd.DataFrame:
    """Pooled, labelled training set across the watchlist.

    Columns: FEATURE_COLUMNS + ['symbol', 'label']. Rows with missing features or
    unknown labels are dropped, leaving only clean, point-in-time examples.
    """
    spy = close_series(session, settings.benchmark_symbol)
    vix = close_series(session, settings.vix_symbol)
    horizon = settings.label_horizon_days

    frames: list[pd.DataFrame] = []
    for symbol in watchlist.symbols:
        bars = load_bars(session, symbol)
        if bars.empty:
            continue
        sector_sym = watchlist.sector_for(symbol)
        sector = close_series(session, sector_sym) if sector_sym else None
        feats = build_features(bars, spy, vix, sector)
        feats = feats.copy()
        feats["label"] = binary_label(bars["close"], spy, horizon)
        feats["symbol"] = symbol
        frames.append(feats)

    if not frames:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, "symbol", "label"])

    pooled = pd.concat(frames).sort_index()
    # rs_sector_20 is legitimately NaN for sector-less tickers; LightGBM handles
    # NaN, so only require the label and the long-warmup features to be present.
    required = [c for c in FEATURE_COLUMNS if c != "rs_sector_20"]
    pooled = pooled.dropna(subset=[*required, "label"])
    return pooled
