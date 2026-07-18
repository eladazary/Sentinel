"""Account & positions from the broker (paper/live), plus latest backtest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep
from sentinel.config import Settings
from sentinel.execution.factory import make_broker
from sentinel.models import BacktestRun
from sentinel.schemas import AccountResponse, BacktestOut, PositionOut

router = APIRouter(tags=["account"])


@router.post("/kill")
def kill_switch(
    flatten: bool = Query(default=True, description="also liquidate open positions"),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Kill switch (spec §6): cancel all working orders and optionally flatten."""
    try:
        broker = make_broker(settings)
        cancelled = broker.cancel_all_orders()
        closed = broker.close_all_positions() if flatten else 0
        return {"ok": True, "orders_cancelled": cancelled, "positions_closed": closed}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


@router.get("/account", response_model=AccountResponse)
def account(settings: Settings = Depends(settings_dep)) -> AccountResponse:
    try:
        broker = make_broker(settings)
        acct = broker.get_account()
        positions = broker.get_positions()
        invested = sum(p.market_value for p in positions.values())
        exposure = (invested / acct.equity * 100.0) if acct.equity > 0 else 0.0
        return AccountResponse(
            available=True,
            mode=settings.mode,
            equity=acct.equity,
            cash=acct.cash,
            buying_power=acct.buying_power,
            exposure_pct=round(exposure, 2),
            positions=[
                PositionOut(
                    symbol=p.symbol,
                    qty=p.qty,
                    avg_entry=p.avg_entry,
                    market_value=p.market_value,
                )
                for p in positions.values()
            ],
        )
    except Exception as exc:  # noqa: BLE001 - broker/network optional at rest
        return AccountResponse(available=False, mode=settings.mode, detail=str(exc))


@router.get("/backtest/latest", response_model=BacktestOut | None)
def latest_backtest(db: Session = Depends(get_db)) -> BacktestOut | None:
    run = db.execute(
        select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        return None
    return BacktestOut(
        id=run.id,
        created_at=run.created_at,
        risk_factor=run.risk_factor,
        start_date=run.start_date,
        end_date=run.end_date,
        n_trades=run.n_trades,
        wf_auc=run.wf_auc,
        metrics=run.metrics,
        benchmarks=run.benchmarks,
        config=run.config,
    )
