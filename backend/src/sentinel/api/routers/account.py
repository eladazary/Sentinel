"""Account & positions from the broker (paper/live), plus latest backtest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep
from sentinel.config import Settings
from sentinel.execution.factory import (
    BrokerStatus,
    BrokerUnavailable,
    describe_broker_error,
    make_broker_with_status,
)
from sentinel.models import BacktestRun, EquitySnapshot
from sentinel.schemas import AccountResponse, BacktestOut, PositionOut

router = APIRouter(tags=["account"])


@router.post("/kill")
def kill_switch(
    flatten: bool = Query(default=True, description="also liquidate open positions"),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Kill switch (spec §6): cancel all working orders and optionally flatten."""
    try:
        broker, status = make_broker_with_status(settings)
        cancelled = broker.cancel_all_orders()
        closed = broker.close_all_positions() if flatten else 0
        # Name the broker: a green result from the sim must not read as
        # "your Alpaca positions are flat".
        return {
            "ok": True,
            "orders_cancelled": cancelled,
            "positions_closed": closed,
            "broker": status.broker,
            "degraded": status.degraded,
            "detail": status.detail,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


@router.get("/account", response_model=AccountResponse)
def account(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> AccountResponse:
    try:
        broker, status = make_broker_with_status(settings)
    except BrokerUnavailable as exc:
        return AccountResponse(
            available=False, mode=settings.mode, source="none",
            broker="alpaca", degraded=True, detail=str(exc),
        )

    # The sim in *this* process is not the worker's sim — they're separate
    # containers — so its 100k-and-no-positions view would be a lie. The equity
    # ledger is the record both processes share.
    if status.broker == "sim":
        return _from_ledger(db, settings, status)

    try:
        acct = broker.get_account()
        positions = broker.get_positions()
    except Exception as exc:  # noqa: BLE001 - credentials can lapse mid-flight
        return _from_ledger(
            db,
            settings,
            BrokerStatus("alpaca", degraded=True, detail=describe_broker_error(exc)),
        )

    invested = sum(p.market_value for p in positions.values())
    exposure = (invested / acct.equity * 100.0) if acct.equity > 0 else 0.0
    return AccountResponse(
        available=True,
        mode=settings.mode,
        source="broker",
        broker="alpaca",
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


def _from_ledger(
    db: Session, settings: Settings, status: BrokerStatus
) -> AccountResponse:
    """Serve equity from the snapshot the worker records each cycle.

    Positions aren't recoverable this way — the ledger stores totals, not
    per-symbol holdings — so the list comes back empty with exposure intact.
    """
    snap = db.execute(
        select(EquitySnapshot).order_by(EquitySnapshot.ts.desc()).limit(1)
    ).scalar_one_or_none()
    if snap is None:
        detail = "no equity snapshots yet — the worker hasn't completed a cycle"
        return AccountResponse(
            available=False, mode=settings.mode, source="none",
            broker=status.broker, degraded=status.degraded,
            detail=f"{status.detail} · {detail}" if status.detail else detail,
        )
    return AccountResponse(
        available=True,
        mode=settings.mode,
        source="ledger",
        broker=status.broker,
        degraded=status.degraded,
        equity=float(snap.equity),
        cash=float(snap.cash),
        buying_power=float(snap.cash),
        exposure_pct=round(float(snap.exposure_pct), 2),
        positions=[],
        as_of=snap.ts,
        detail=status.detail,
    )


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
