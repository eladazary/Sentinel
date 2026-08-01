"""Performance: equity curve vs SPY + drawdown (spec §9)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep
from sentinel.config import Settings, get_settings
from sentinel.logging_config import get_logger
from sentinel.models import DailyBar, Decision, EquitySnapshot
from sentinel.schemas import PerformancePoint, PerformanceResponse, PerformanceSummary

router = APIRouter(tags=["performance"])

log = get_logger(__name__)


@router.get("/performance", response_model=PerformanceResponse)
def performance(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> PerformanceResponse:
    snaps = list(
        db.execute(
            select(EquitySnapshot).order_by(EquitySnapshot.ts.asc())
        ).scalars()
    )
    # SPY closes for the benchmark overlay, restricted to the window the equity
    # curve covers. Normalizing from the first bar in the whole table would plot
    # years of index growth against months of paper equity on one axis.
    spy_rows = []
    if snaps:
        spy_rows = db.execute(
            select(DailyBar.ts, DailyBar.close)
            .where(
                DailyBar.symbol == settings.benchmark_symbol,
                DailyBar.ts >= snaps[0].ts,
            )
            .order_by(DailyBar.ts.asc())
        ).all()
    spy_first = float(spy_rows[0][1]) if spy_rows else None

    def spy_at(ts) -> float | None:
        if not spy_rows or spy_first is None:
            return None
        # Last SPY close at/before ts, rebased so the overlay starts level with
        # the equity curve.
        val = None
        for bts, close in spy_rows:
            if bts <= ts:
                val = float(close)
            else:
                break
        # Before the first bar in the window, the baseline itself is the level.
        return ((val or spy_first) / spy_first) * settings.starting_equity

    points: list[PerformancePoint] = []
    peak = settings.starting_equity
    for s in snaps:
        eq = float(s.equity)
        peak = max(peak, eq)
        dd = (eq / peak - 1.0) * 100.0 if peak > 0 else 0.0
        points.append(
            PerformancePoint(ts=s.ts, equity=eq, drawdown_pct=round(dd, 3), spy=spy_at(s.ts))
        )
    return PerformanceResponse(
        starting_equity=settings.starting_equity,
        points=points,
        summary=_summarize(db, settings, snaps, points, spy_at),
    )


def _broker_equity() -> tuple[float | None, str, datetime | None]:
    """Live equity from the venue, or (None, ...) if it can't be trusted.

    Refuses a degraded broker: the in-memory sim reports a pristine 100k that
    would read as "your money is fine" while the real account is unreachable.
    """
    from sentinel.execution.factory import make_broker_with_status

    try:
        broker, status = make_broker_with_status(get_settings())
        if status.degraded or status.broker != "alpaca":
            return None, "", None
        return float(broker.get_account().equity), "broker", datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001 - fall back to the ledger, never fail the view
        log.warning("could not read live equity from the broker", exc_info=True)
        return None, "", None


def _summarize(
    db: Session,
    settings: Settings,
    snaps: list[EquitySnapshot],
    points: list[PerformancePoint],
    spy_at,
) -> PerformanceSummary | None:
    """Current money and realized yield for the forward paper run.

    Headline figures come from the ``live`` series only. The replay is a
    backtest written into the same table; splicing the two and dividing the last
    equity by the first measures the step between them, not performance.
    """
    if not snaps:
        return None

    start = settings.starting_equity
    live_rows = [s for s in snaps if s.source == "live"]
    replay_rows = [s for s in snaps if s.source == "replay"]

    # Prefer the broker. The ledger is only as current as the last cycle the
    # *local* worker completed, so a stopped worker — or a second machine sharing
    # one Alpaca account but keeping its own database — reported $100,000 and
    # 0.00% while real positions were open at the venue. The venue is the truth
    # about money; the ledger is this machine's record of it.
    equity, equity_source, as_of = _broker_equity()
    if equity is None:
        if live_rows:
            equity, equity_source = float(live_rows[-1].equity), "ledger"
            as_of = live_rows[-1].ts
        else:
            # Neither the venue nor a forward row: say baseline rather than
            # borrow the replay's closing equity, which was never real money.
            equity, equity_source, as_of = start, "baseline", None
    pnl = equity - start

    def endpoint_return(rows: list[EquitySnapshot]) -> float | None:
        if len(rows) < 2 or float(rows[0].equity) <= 0:
            return None
        return float(rows[-1].equity) / float(rows[0].equity) - 1.0

    replay_ret = endpoint_return(replay_rows)

    opened = db.execute(
        select(func.count())
        .select_from(Decision)
        .where(Decision.broker_order_id.isnot(None))
    ).scalar_one()

    note = None
    if opened == 0:
        note = (
            "No position has ever been opened — every decision was skipped, so "
            "the return is 0% by construction, not by losing money."
        )

    benchmark = None
    live_points = [p for p, s in zip(points, snaps) if s.source == "live"]
    if live_points and live_points[-1].spy is not None and start > 0:
        benchmark = round((live_points[-1].spy / start - 1.0) * 100.0, 2)

    live_dd = min((p.drawdown_pct for p in live_points), default=0.0)

    return PerformanceSummary(
        equity=round(equity, 2),
        starting_equity=start,
        pnl=round(pnl, 2),
        # Consistent with pnl by construction, so the two can never disagree.
        return_pct=round(pnl / start * 100.0, 3) if start > 0 else 0.0,
        max_drawdown_pct=round(live_dd, 3),
        benchmark_return_pct=benchmark,
        replay_return_pct=round(replay_ret * 100.0, 3) if replay_ret is not None else None,
        positions_opened=int(opened),
        as_of=as_of,
        equity_source=equity_source,
        note=note,
    )
