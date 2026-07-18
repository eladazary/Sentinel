"""Walk-forward portfolio backtester (spec §8).

Consumes out-of-sample model predictions (from ``model.walkforward``) and daily
bars, then simulates a long-only portfolio through the same signal engine and
risk manager the live loop uses — so a backtest and live trading share one code
path. Realistic frictions (slippage + commission), ATR-based bracket stops, and
the hard breakers are all applied. Decisions are made on the close of day t and
filled at that close; stops/targets are only evaluated from t+1 onward, so there
is no same-bar lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from sentinel.backtest.metrics import (
    PerformanceMetrics,
    basket_buy_and_hold,
    buy_and_hold_equity,
    compute_metrics,
)
from sentinel.risk.breakers import check_breakers
from sentinel.risk.manager import size_position
from sentinel.risk.profile import RiskProfile, risk_profile
from sentinel.signals.engine import derive_signal


@dataclass
class BacktestConfig:
    risk_factor: int = 5
    starting_equity: float = 100_000.0
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 12.0
    reward_risk: float = 2.0


@dataclass
class Position:
    symbol: str
    shares: int
    entry_price: float
    entry_date: pd.Timestamp
    stop: float
    take_profit: float


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    return_pct: float
    exit_reason: str


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    exposure: pd.Series
    metrics: PerformanceMetrics
    benchmarks: dict[str, PerformanceMetrics]
    trades: list[Trade] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "config": self.config,
            "strategy": self.metrics.as_dict(),
            "benchmarks": {k: v.as_dict() for k, v in self.benchmarks.items()},
            "n_trades": len(self.trades),
            "start": str(self.equity_curve.index[0]) if len(self.equity_curve) else None,
            "end": str(self.equity_curve.index[-1]) if len(self.equity_curve) else None,
        }


def _buy_fill(price: float, slippage_bps: float) -> float:
    return price * (1 + slippage_bps / 10_000.0)


def _sell_fill(price: float, slippage_bps: float) -> float:
    return price * (1 - slippage_bps / 10_000.0)


class Backtester:
    """Runs one portfolio simulation over prepared per-symbol frames.

    Each per-symbol frame must be indexed by date with columns:
    ``close, high, low, atr_pct, score`` (score = technical conviction −100..100).
    """

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.profile: RiskProfile = risk_profile(config.risk_factor)

    def run(
        self,
        symbol_frames: dict[str, pd.DataFrame],
        benchmark_close: pd.Series | None = None,
    ) -> BacktestResult:
        cfg = self.cfg
        gate = self.profile.min_conviction

        # Master trading calendar = sorted union of all symbols' dates.
        all_dates = sorted(set().union(*[set(df.index) for df in symbol_frames.values()]))
        cash = cfg.starting_equity
        positions: dict[str, Position] = {}
        trades: list[Trade] = []
        equity_points: list[tuple[pd.Timestamp, float]] = []
        exposure_points: list[tuple[pd.Timestamp, float]] = []

        high_water_mark = cfg.starting_equity
        locked = False  # drawdown breaker latch

        def mark_to_market(date: pd.Timestamp) -> float:
            val = cash
            for pos in positions.values():
                px = _price(symbol_frames[pos.symbol], date, "close")
                if px is not None:
                    val += pos.shares * px
            return val

        def invested_value(date: pd.Timestamp) -> float:
            v = 0.0
            for pos in positions.values():
                px = _price(symbol_frames[pos.symbol], date, "close")
                if px is not None:
                    v += pos.shares * px
            return v

        exited_today: set[str] = set()

        def close_position(
            pos: Position, date: pd.Timestamp, price: float, reason: str
        ) -> None:
            nonlocal cash
            exited_today.add(pos.symbol)
            fill = _sell_fill(price, cfg.slippage_bps)
            proceeds = pos.shares * fill - pos.shares * cfg.commission_per_share
            cash += proceeds
            pnl = (fill - pos.entry_price) * pos.shares
            trades.append(
                Trade(
                    symbol=pos.symbol,
                    entry_date=pos.entry_date,
                    exit_date=date,
                    entry_price=pos.entry_price,
                    exit_price=fill,
                    shares=pos.shares,
                    pnl=round(pnl, 2),
                    return_pct=round(fill / pos.entry_price - 1.0, 4),
                    exit_reason=reason,
                )
            )

        prev_equity = cfg.starting_equity
        for date in all_dates:
            day_start_equity = prev_equity

            # --- hard breakers evaluated on the mark before acting ---
            equity_now = mark_to_market(date)
            high_water_mark = max(high_water_mark, equity_now)
            br = check_breakers(
                equity=equity_now,
                day_start_equity=day_start_equity,
                high_water_mark=high_water_mark,
                daily_loss_pct=cfg.daily_loss_pct,
                max_drawdown_pct=cfg.max_drawdown_pct,
            )
            halt_new = br.any_tripped or locked
            if br.drawdown_tripped:
                locked = True
            if br.any_tripped:
                # Flatten everything at the current close.
                for sym in list(positions):
                    px = _price(symbol_frames[sym], date, "close")
                    if px is not None:
                        close_position(positions.pop(sym), date, px, "breaker")

            # --- manage existing positions (stops/targets/signal exits) ---
            for sym in list(positions):
                pos = positions[sym]
                if pos.entry_date == date:
                    continue  # no same-bar stop check on entry day
                row = _row(symbol_frames[sym], date)
                if row is None:
                    continue
                low, high, close = row["low"], row["high"], row["close"]
                if low <= pos.stop:
                    close_position(positions.pop(sym), date, pos.stop, "stop")
                    continue
                if high >= pos.take_profit:
                    close_position(positions.pop(sym), date, pos.take_profit, "target")
                    continue
                sig = derive_signal(row["score"], gate, has_position=True)
                if sig == "SELL":
                    close_position(positions.pop(sym), date, close, "signal")

            # --- new entries ---
            new_today = 0
            if not halt_new:
                for sym, df in symbol_frames.items():
                    if sym in positions or sym in exited_today:
                        continue  # no same-day re-entry after an exit (anti-whipsaw)
                    if new_today >= self.profile.max_new_positions_per_day:
                        break
                    row = _row(df, date)
                    if row is None:
                        continue
                    score = row["score"]
                    if derive_signal(score, gate, has_position=False) != "BUY":
                        continue
                    close = row["close"]
                    atr = row["atr_pct"] * close if row["atr_pct"] == row["atr_pct"] else 0.0
                    equity_now = mark_to_market(date)
                    sizing = size_position(
                        equity=equity_now,
                        price=close,
                        atr=atr,
                        profile=self.profile,
                        current_exposure_value=invested_value(date),
                        reward_risk=cfg.reward_risk,
                    )
                    if sizing.blocked:
                        continue
                    fill = _buy_fill(close, cfg.slippage_bps)
                    cost = sizing.shares * fill + sizing.shares * cfg.commission_per_share
                    if cost > cash:
                        continue
                    cash -= cost
                    positions[sym] = Position(
                        symbol=sym,
                        shares=sizing.shares,
                        entry_price=fill,
                        entry_date=date,
                        stop=sizing.stop_price,
                        take_profit=sizing.take_profit,
                    )
                    new_today += 1

            equity_eod = mark_to_market(date)
            prev_equity = equity_eod
            high_water_mark = max(high_water_mark, equity_eod)
            equity_points.append((date, equity_eod))
            invested = invested_value(date)
            exposure_points.append(
                (date, invested / equity_eod if equity_eod > 0 else 0.0)
            )

        equity_curve = pd.Series(
            [v for _, v in equity_points], index=[d for d, _ in equity_points]
        )
        exposure = pd.Series(
            [v for _, v in exposure_points], index=[d for d, _ in exposure_points]
        )
        trade_returns = [t.return_pct for t in trades]
        metrics = compute_metrics(
            equity_curve, trade_returns=trade_returns, exposure=exposure
        )

        benchmarks = self._benchmarks(symbol_frames, benchmark_close, equity_curve.index)

        return BacktestResult(
            equity_curve=equity_curve,
            exposure=exposure,
            metrics=metrics,
            benchmarks=benchmarks,
            trades=trades,
            config={
                "risk_factor": cfg.risk_factor,
                "starting_equity": cfg.starting_equity,
                "slippage_bps": cfg.slippage_bps,
                "commission_per_share": cfg.commission_per_share,
                "conviction_gate": gate,
            },
        )

    def _benchmarks(
        self,
        symbol_frames: dict[str, pd.DataFrame],
        benchmark_close: pd.Series | None,
        index: pd.Index,
    ) -> dict[str, PerformanceMetrics]:
        out: dict[str, PerformanceMetrics] = {}
        # Equal-weight basket buy & hold of the traded universe.
        closes = {s: df["close"] for s, df in symbol_frames.items()}
        basket = basket_buy_and_hold(closes, self.cfg.starting_equity).reindex(
            index
        ).ffill().dropna()
        if len(basket) >= 2:
            out["basket_buy_hold"] = compute_metrics(basket)
        if benchmark_close is not None:
            spy = buy_and_hold_equity(
                benchmark_close.reindex(index).ffill().dropna(),
                self.cfg.starting_equity,
            )
            if len(spy) >= 2:
                out["spy_buy_hold"] = compute_metrics(spy)
        return out


def _row(df: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    try:
        return df.loc[date]
    except KeyError:
        return None


def _price(df: pd.DataFrame, date: pd.Timestamp, col: str) -> float | None:
    try:
        return float(df.at[date, col])
    except KeyError:
        return None
