"""Performance metrics for an equity curve (spec §8).

CAGR, Sharpe, Sortino, max drawdown, win rate, and exposure-adjusted return,
computed from a daily equity series and per-trade results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class PerformanceMetrics:
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float  # negative fraction, e.g. -0.18
    volatility: float
    total_return: float
    win_rate: float | None
    n_trades: int
    avg_exposure: float
    exposure_adjusted_return: float

    def as_dict(self) -> dict:
        return asdict(self)


def _daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = len(equity) / TRADING_DAYS
    if years <= 0:
        return 0.0
    growth = equity.iloc[-1] / equity.iloc[0]
    if growth <= 0:
        return -1.0
    return float(growth ** (1.0 / years) - 1.0)


def sharpe(returns: pd.Series, rf: float = 0.0) -> float:
    if returns.std(ddof=0) == 0 or returns.empty:
        return 0.0
    excess = returns - rf / TRADING_DAYS
    return float(excess.mean() / returns.std(ddof=0) * np.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series, rf: float = 0.0) -> float:
    if returns.empty:
        return 0.0
    excess = returns - rf / TRADING_DAYS
    downside = returns[returns < 0]
    dd = downside.std(ddof=0)
    if dd == 0 or np.isnan(dd):
        return 0.0
    return float(excess.mean() / dd * np.sqrt(TRADING_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def compute_metrics(
    equity: pd.Series,
    *,
    trade_returns: list[float] | None = None,
    exposure: pd.Series | None = None,
) -> PerformanceMetrics:
    """Assemble the full metrics bundle from an equity curve."""
    equity = equity.dropna()
    rets = _daily_returns(equity)
    trades = trade_returns or []
    wins = [t for t in trades if t > 0]
    win_rate = (len(wins) / len(trades)) if trades else None
    avg_exposure = float(exposure.mean()) if exposure is not None and len(exposure) else 0.0
    total_return = (
        float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) >= 2 else 0.0
    )
    _cagr = cagr(equity)
    exp_adj = _cagr / avg_exposure if avg_exposure > 0 else 0.0

    return PerformanceMetrics(
        cagr=round(_cagr, 4),
        sharpe=round(sharpe(rets), 3),
        sortino=round(sortino(rets), 3),
        max_drawdown=round(max_drawdown(equity), 4),
        volatility=round(float(rets.std(ddof=0) * np.sqrt(TRADING_DAYS)), 4),
        total_return=round(total_return, 4),
        win_rate=round(win_rate, 4) if win_rate is not None else None,
        n_trades=len(trades),
        avg_exposure=round(avg_exposure, 4),
        exposure_adjusted_return=round(exp_adj, 4),
    )


def buy_and_hold_equity(close: pd.Series, starting_equity: float) -> pd.Series:
    """Equity curve of buying the asset at the first bar and holding."""
    close = close.dropna()
    if close.empty:
        return pd.Series(dtype=float)
    return starting_equity * (close / close.iloc[0])


def basket_buy_and_hold(
    closes: dict[str, pd.Series], starting_equity: float
) -> pd.Series:
    """Equal-weight buy-and-hold of a basket, rebalanced only at inception."""
    if not closes:
        return pd.Series(dtype=float)
    per = starting_equity / len(closes)
    curves = [buy_and_hold_equity(c, per) for c in closes.values()]
    combined = pd.concat(curves, axis=1).ffill().dropna()
    return combined.sum(axis=1)
