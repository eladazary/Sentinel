"""Feature-matrix and label construction tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.features.engineering import FEATURE_COLUMNS, build_features
from sentinel.features.labels import binary_label, forward_excess_return


@pytest.fixture
def synth_bars():
    idx = pd.date_range("2020-01-01", periods=400, freq="B", tz="UTC")
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, size=len(idx)))
    close = np.abs(close) + 10
    df = pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, len(idx))),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, len(idx)).astype(float),
        },
        index=idx,
    )
    return df


def test_build_features_columns_and_index(synth_bars):
    spy = synth_bars["close"] * 0.9 + 50
    vix = pd.Series(20 + np.sin(np.arange(len(synth_bars))) * 3, index=synth_bars.index)
    feats = build_features(synth_bars, spy, vix, sector_close=spy)
    assert list(feats.columns) == FEATURE_COLUMNS
    assert feats.index.equals(synth_bars.index)
    # After the 252-day warmup, rows should be fully populated.
    assert feats.iloc[300].notna().all()


def test_rs_sector_nan_when_missing(synth_bars):
    spy = synth_bars["close"]
    vix = pd.Series(20.0, index=synth_bars.index)
    feats = build_features(synth_bars, spy, vix, sector_close=None)
    assert feats["rs_sector_20"].isna().all()


def test_forward_excess_return_no_lookahead():
    idx = pd.date_range("2021-01-01", periods=15, freq="B")
    close = pd.Series(np.arange(100, 115, dtype=float), index=idx)
    bench = pd.Series(np.full(15, 100.0), index=idx)  # flat benchmark
    exc = forward_excess_return(close, bench, horizon=5)
    # Last `horizon` rows are unknown future -> NaN.
    assert exc.iloc[-5:].isna().all()
    # Rising stock vs flat bench -> positive excess early on.
    assert exc.iloc[0] > 0


def test_binary_label_values():
    idx = pd.date_range("2021-01-01", periods=12, freq="B")
    close = pd.Series(np.arange(100, 112, dtype=float), index=idx)
    bench = pd.Series(np.full(12, 100.0), index=idx)
    lab = binary_label(close, bench, horizon=3)
    valid = lab.dropna()
    assert set(valid.unique()).issubset({0.0, 1.0})
    assert valid.iloc[0] == 1.0  # up vs flat bench
