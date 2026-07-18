"""Signal engine tests: fusion, signal derivation, drivers."""

from __future__ import annotations

import pandas as pd

from sentinel.signals.engine import SubScore, derive_signal, fuse
from sentinel.signals.technical_signal import technical_drivers


def test_fuse_single_model_is_weight_normalized():
    ss = SubScore("technical", score=40.0, confidence=0.6, weight=0.45, drivers=["a"])
    c = fuse([ss])
    assert c.conviction == 40.0  # single active model -> its own score
    assert c.confidence == 0.6
    assert c.per_model == {"technical": 40.0}
    assert c.drivers == ["a"]


def test_fuse_ignores_zero_weight():
    active = SubScore("technical", 50.0, 0.8, 0.45)
    inactive = SubScore("news", 90.0, 1.0, 0.0)
    c = fuse([active, inactive])
    assert c.conviction == 50.0
    assert "news" not in c.per_model


def test_fuse_disagreement_lowers_confidence():
    a = SubScore("technical", 60.0, 1.0, 0.5)
    b = SubScore("news", -60.0, 1.0, 0.5)
    c = fuse([a, b])
    assert c.conviction == 0.0  # opposing, equal weight
    assert c.confidence < 1.0  # disagreement penalty applied


def test_fuse_empty():
    c = fuse([])
    assert c.conviction == 0.0 and c.confidence == 0.0


def test_derive_signal_open():
    assert derive_signal(60, gate=50, has_position=False) == "BUY"
    assert derive_signal(40, gate=50, has_position=False) == "PASS"


def test_derive_signal_manage_position():
    assert derive_signal(70, gate=50, has_position=True) == "HOLD"
    assert derive_signal(-10, gate=50, has_position=True) == "TRIM"
    assert derive_signal(-50, gate=50, has_position=True) == "SELL"


def test_technical_drivers_readable():
    f = pd.Series(
        {
            "dist_ma50": 0.04,
            "ma50_over_ma200": 0.02,
            "rsi14": 72.0,
            "macd_hist_norm": 0.001,
            "bb_pctb": 0.95,
            "vol_z": 2.4,
            "rs_spy_20": 0.031,
            "rs_sector_20": 0.01,
            "vix_pctile": 0.85,
        }
    )
    drivers = technical_drivers(f, top_n=3)
    assert len(drivers) == 3
    assert all(isinstance(d, str) and d for d in drivers)
