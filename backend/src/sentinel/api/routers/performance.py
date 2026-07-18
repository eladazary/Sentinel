"""Performance: equity curve vs SPY + drawdown (spec §9)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep
from sentinel.config import Settings
from sentinel.models import DailyBar, EquitySnapshot
from sentinel.schemas import PerformancePoint, PerformanceResponse

router = APIRouter(tags=["performance"])


@router.get("/performance", response_model=PerformanceResponse)
def performance(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> PerformanceResponse:
    snaps = list(
        db.execute(
            select(EquitySnapshot).order_by(EquitySnapshot.ts.asc())
        ).scalars()
    )
    # SPY closes for a normalized benchmark overlay.
    spy_rows = db.execute(
        select(DailyBar.ts, DailyBar.close)
        .where(DailyBar.symbol == settings.benchmark_symbol)
        .order_by(DailyBar.ts.asc())
    ).all()
    spy_first = float(spy_rows[0][1]) if spy_rows else None

    def spy_at(ts) -> float | None:
        if not spy_rows or spy_first is None:
            return None
        # Last SPY close at/before ts, normalized to starting equity.
        val = None
        for bts, close in spy_rows:
            if bts <= ts:
                val = float(close)
            else:
                break
        return (val / spy_first) * settings.starting_equity if val else None

    points: list[PerformancePoint] = []
    peak = settings.starting_equity
    for s in snaps:
        eq = float(s.equity)
        peak = max(peak, eq)
        dd = (eq / peak - 1.0) * 100.0 if peak > 0 else 0.0
        points.append(
            PerformancePoint(ts=s.ts, equity=eq, drawdown_pct=round(dd, 3), spy=spy_at(s.ts))
        )
    return PerformanceResponse(starting_equity=settings.starting_equity, points=points)
