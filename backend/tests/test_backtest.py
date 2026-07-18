"""Backtest metrics and engine mechanics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.backtest.engine import Backtester, BacktestConfig
from sentinel.backtest.metrics import cagr, max_drawdown, sharpe


def test_cagr_doubling_in_one_year():
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    equity = pd.Series(np.linspace(100_000, 200_000, 252), index=idx)
    assert cagr(equity) > 0.9  # ~doubled in a year


def test_max_drawdown():
    equity = pd.Series([100.0, 120.0, 90.0, 110.0])
    assert round(max_drawdown(equity), 3) == round(90 / 120 - 1, 3)


def test_sharpe_zero_when_flat():
    equity_rets = pd.Series([0.0, 0.0, 0.0])
    assert sharpe(equity_rets) == 0.0


def _frame(closes, highs, lows, score, atr_pct=0.02):
    idx = pd.date_range("2021-01-01", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "close": closes,
            "high": highs,
            "low": lows,
            "atr_pct": atr_pct,
            "score": score,
        },
        index=idx,
    )


def test_engine_takes_profit():
    # Enter at 100, target = 100 + 2*(2.5*2) = 110; price rallies through 110.
    closes = [100, 102, 105, 108, 112, 112]
    highs = [100, 103, 106, 109, 113, 113]
    lows = [99, 101, 104, 107, 111, 111]
    frame = _frame(closes, highs, lows, score=60)  # above gate 50 at RF5
    bt = Backtester(BacktestConfig(risk_factor=5, slippage_bps=0))
    res = bt.run({"AAA": frame})
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "target"
    assert res.trades[0].pnl > 0


def test_engine_stops_out():
    # Enter at 100, stop = 100 - 2.5*2 = 95; price craters below 95.
    closes = [100, 98, 94, 90, 90]
    highs = [100, 99, 96, 92, 92]
    lows = [99, 96, 93, 89, 89]
    frame = _frame(closes, highs, lows, score=60)
    bt = Backtester(BacktestConfig(risk_factor=5, slippage_bps=0))
    res = bt.run({"AAA": frame})
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "stop"
    assert res.trades[0].pnl < 0


def test_engine_no_trade_below_gate():
    closes = [100, 101, 102, 103]
    frame = _frame(closes, closes, closes, score=10)  # below gate 50
    bt = Backtester(BacktestConfig(risk_factor=5))
    res = bt.run({"AAA": frame})
    assert res.trades == []


def test_engine_produces_equity_curve_and_benchmarks():
    rng = np.random.default_rng(3)
    n = 60
    closes = list(100 + np.cumsum(rng.normal(0.2, 1, n)))
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    frame = _frame(closes, highs, lows, score=55)
    spy = pd.Series(closes, index=frame.index)
    res = Backtester(BacktestConfig(risk_factor=5)).run({"AAA": frame}, benchmark_close=spy)
    assert len(res.equity_curve) == n
    assert "spy_buy_hold" in res.benchmarks
    assert "basket_buy_hold" in res.benchmarks
