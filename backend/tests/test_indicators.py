"""Technical indicator unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.features import indicators as ind


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = ind.sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == 2.0
    assert out.iloc[4] == 4.0


def test_rsi_all_gains_is_100():
    s = pd.Series(np.arange(1, 40, dtype=float))  # strictly increasing
    rsi = ind.rsi(s, 14)
    assert rsi.dropna().iloc[-1] == 100.0


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(size=200)))
    rsi = ind.rsi(s, 14).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_bollinger_pctb_midband_is_half():
    # Constant-trend data: at the middle band %B ~ 0.5 region behaviour.
    s = pd.Series(np.linspace(100, 120, 60))
    b = ind.bollinger_pctb(s, 20, 2.0).dropna()
    assert (b > 0).all()  # rising series stays in upper half


def test_atr_positive():
    n = 60
    rng = np.random.default_rng(1)
    close = pd.Series(100 + np.cumsum(rng.normal(size=n)))
    high = close + 1.0
    low = close - 1.0
    atr = ind.atr(high, low, close, 14).dropna()
    assert (atr > 0).all()


def test_volume_zscore_zero_mean_ish():
    v = pd.Series(np.r_[np.full(30, 1000.0), [5000.0]])
    z = ind.volume_zscore(v, 30)
    assert z.iloc[-1] > 3  # a 5x spike is a large positive z


def test_overnight_gap():
    close = pd.Series([100.0, 110.0, 121.0])
    open_ = pd.Series([100.0, 105.0, 110.0])
    gap = ind.overnight_gap(open_, close)
    assert round(gap.iloc[1], 4) == 0.05  # 105 vs prev close 100


def test_rolling_percentile_range():
    s = pd.Series(np.arange(300, dtype=float))
    p = ind.rolling_percentile(s, 252).dropna()
    assert (p > 0).all() and (p <= 1.0).all()
    assert p.iloc[-1] == 1.0  # last value is the max in its window
