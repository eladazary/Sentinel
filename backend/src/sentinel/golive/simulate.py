"""Accelerated historical dry-run.

Replays the most recent N months of the walk-forward backtest into the *live*
tables (equity snapshots + decision log + the dry-run clock) so the dashboard and
go-live gate populate immediately — instead of waiting ~3 months of wall-clock.

HONESTY NOTE: this is an accelerated *historical* replay, not a true forward
dry-run. It uses the technical model only (news/social can't be reconstructed
historically) and inherits backtest optimism. Treat it as a fast go/no-go read,
not as satisfaction of the real forward gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy.orm import Session

from sentinel.backtest.runner import run_backtest
from sentinel.config import Settings, Watchlist
from sentinel.execution import decision_log as dlog
from sentinel.logging_config import get_logger
from sentinel.models import BacktestRun
from sentinel.system_state import get_state

log = get_logger("sentinel.simulate")


def simulate_dryrun(
    session: Session, watchlist: Watchlist, settings: Settings, *, months: int = 3
) -> dict:
    result, wf = run_backtest(session, watchlist, settings)
    eq = result.equity_curve
    if eq.empty:
        return {"ok": False, "reason": "no backtest equity — backfill + data required"}

    end = eq.index[-1]
    window_start = end - timedelta(days=int(months * 31))
    eqw = eq[eq.index >= window_start]
    expw = result.exposure.reindex(eqw.index).fillna(0.0)
    if len(eqw) < 2:
        return {"ok": False, "reason": "not enough history for the requested window"}

    # 1. equity snapshots (mode DRY_RUN; on-conflict-nothing keeps real rows).
    for ts, equity in eqw.items():
        exposure = float(expw.get(ts, 0.0))
        dlog.record_equity(
            session, ts=_utc(ts), equity=float(equity),
            cash=float(equity) * (1 - exposure), exposure_pct=exposure * 100.0,
            mode="DRY_RUN",
        )

    # 2. decision log from in-window trades.
    rf = settings.default_risk_factor
    n_dec = 0
    for t in result.trades:
        if _utc(t.entry_date) >= _utc(window_start):
            dlog.log_decision(
                session, ts=_utc(t.entry_date), symbol=t.symbol, action="OPEN",
                signal="BUY", conviction=0.0, confidence=0.0, risk_factor=rf,
                mode="DRY_RUN",
                reason=f"[sim] BUY {t.shares}@{t.entry_price:.2f} · stop-based bracket",
                drivers=["historical replay"],
            )
            n_dec += 1
        if _utc(t.exit_date) >= _utc(window_start):
            dlog.log_decision(
                session, ts=_utc(t.exit_date), symbol=t.symbol, action="EXIT",
                signal="SELL", conviction=0.0, confidence=0.0, risk_factor=rf,
                mode="DRY_RUN",
                reason=(f"[sim] {t.exit_reason} {t.shares}@{t.exit_price:.2f} "
                        f"· {t.return_pct*100:+.1f}%"),
                drivers=["historical replay"],
            )
            n_dec += 1

    # 3. start the dry-run clock at the window start.
    state = get_state(session)
    state.dry_run_started_at = _utc(window_start)
    state.updated_at = datetime.now(timezone.utc)

    # 4. store the backtest run (sets the drawdown expectation for the gate).
    session.add(BacktestRun(
        created_at=datetime.now(timezone.utc), risk_factor=result.config.get("risk_factor", rf),
        start_date=str(eqw.index[0]), end_date=str(eqw.index[-1]),
        n_trades=len(result.trades), wf_auc=wf.auc(),
        metrics=result.metrics.as_dict(),
        benchmarks={k: v.as_dict() for k, v in result.benchmarks.items()},
        config=result.config,
    ))

    summary = {
        "ok": True,
        "window_start": str(window_start.date()),
        "window_end": str(end.date()),
        "trading_days": int(len(eqw)),
        "decisions_written": n_dec,
        "wf_auc": wf.auc(),
        "strategy": result.metrics.as_dict(),
        "benchmarks": {k: v.as_dict() for k, v in result.benchmarks.items()},
    }
    log.info("simulated dry-run over %d days (%d decisions)", len(eqw), n_dec)
    return summary


def _utc(ts) -> datetime:
    ts = pd.Timestamp(ts).to_pydatetime()
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
