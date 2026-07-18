"""Glue: turn stored bars into a walk-forward backtest.

Builds features + labels, generates out-of-sample predictions, assembles the
per-symbol frames the Backtester needs, and runs it against the SPY benchmark.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from sentinel.backtest.engine import Backtester, BacktestConfig, BacktestResult
from sentinel.config import Settings, Watchlist
from sentinel.features.dataset import (
    build_symbol_features,
    build_training_frame,
    close_series,
    load_bars,
)
from sentinel.features.engineering import FEATURE_COLUMNS
from sentinel.model.walkforward import WalkForwardResult, walk_forward_predict


def run_backtest(
    session: Session,
    watchlist: Watchlist,
    settings: Settings,
    *,
    risk_factor: int | None = None,
) -> tuple[BacktestResult, WalkForwardResult]:
    """Run a full walk-forward backtest over the watchlist. Returns
    (backtest_result, walk_forward_result) so callers can report model quality
    alongside portfolio performance."""
    training = build_training_frame(session, watchlist, settings)
    wf = walk_forward_predict(
        training,
        FEATURE_COLUMNS,
        train_days=settings.walkforward_train_days,
        step_days=settings.walkforward_step_days,
    )

    symbol_frames: dict[str, pd.DataFrame] = {}
    preds = wf.predictions
    for symbol in watchlist.symbols:
        sym_scores = preds[preds["symbol"] == symbol]["score"] if not preds.empty else pd.Series(dtype=float)
        if sym_scores.empty:
            continue
        bars = load_bars(session, symbol)
        feats = build_symbol_features(session, watchlist, symbol, settings)
        frame = pd.DataFrame(
            {
                "close": bars["close"],
                "high": bars["high"],
                "low": bars["low"],
                "atr_pct": feats["atr_pct"],
            }
        )
        frame["score"] = sym_scores
        frame = frame.dropna(subset=["close", "high", "low", "score"])
        if not frame.empty:
            symbol_frames[symbol] = frame

    cfg = BacktestConfig(
        risk_factor=risk_factor or settings.default_risk_factor,
        starting_equity=settings.starting_equity,
        slippage_bps=settings.slippage_bps,
        commission_per_share=settings.commission_per_share,
        daily_loss_pct=settings.daily_loss_breaker_pct,
        max_drawdown_pct=settings.max_drawdown_breaker_pct,
    )
    spy = close_series(session, settings.benchmark_symbol)
    result = Backtester(cfg).run(symbol_frames, benchmark_close=spy)
    return result, wf
