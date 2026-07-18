"""Pure technical-indicator functions.

Each takes pandas Series and returns a Series aligned to the input index. No I/O,
no lookahead (every value at index t uses only data at or before t).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """Simple return over ``periods`` bars."""
    return series.pct_change(periods=periods)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/window.
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> no losses -> RSI 100.
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def macd_histogram(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.Series:
    """MACD histogram = (EMA_fast - EMA_slow) - signal EMA."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line - signal_line


def bollinger_pctb(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger %B: 0 at lower band, 1 at upper band (can exceed [0,1])."""
    mid = sma(close, window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower).replace(0.0, np.nan)
    return (close - lower) / width


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """Average True Range (Wilder). Absolute price units."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def volume_zscore(volume: pd.Series, window: int = 30) -> pd.Series:
    """Z-score of volume vs its trailing ``window`` mean/std."""
    mean = volume.rolling(window, min_periods=window).mean()
    std = volume.rolling(window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
    return (volume - mean) / std


def overnight_gap(open_: pd.Series, close: pd.Series) -> pd.Series:
    """Open vs previous close, as a fraction."""
    return open_ / close.shift(1) - 1.0


def rolling_percentile(series: pd.Series, window: int = 252) -> pd.Series:
    """Percentile rank (0..1) of the latest value within the trailing window."""

    def _rank(x: np.ndarray) -> float:
        last = x[-1]
        return float((x <= last).mean())

    return series.rolling(window, min_periods=window).apply(_rank, raw=True)
