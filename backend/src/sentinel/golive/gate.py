"""Go-live gate: evaluate the five criteria that must ALL pass before LIVE.

Spec §10.4:
  1. ≥ 60 trading days on paper
  2. positive risk-adjusted excess return vs basket buy-and-hold
  3. max drawdown within model expectation (the backtest's drawdown)
  4. zero breaker malfunctions (every breaker firing acknowledged as correct)
  5. manual review of ≥ 20 random decision logs
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentinel.backtest.metrics import max_drawdown, sharpe
from sentinel.config import Settings
from sentinel.models import (
    BacktestRun,
    DailyBar,
    DecisionReview,
    EquitySnapshot,
)
from sentinel.system_state import (
    breaker_event_count,
    get_state,
    trading_days_count,
)


@dataclass
class Criterion:
    key: str
    label: str
    passed: bool
    detail: str
    value: float | int | None = None
    threshold: float | int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateResult:
    passed: bool
    dry_run_started_at: str | None
    criteria: list[Criterion] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "dry_run_started_at": self.dry_run_started_at,
            "criteria": [c.as_dict() for c in self.criteria],
        }


def _equity_series(session: Session, source: str | None = None) -> pd.Series:
    """Equity over time, optionally restricted to one source.

    Replay and live rows are two unrelated series spliced end to end: the
    replay's final equity and the live series' opening equity have no
    relationship, so spanning both makes `last / first - 1` a measurement of
    that discontinuity rather than a return.
    """
    stmt = select(EquitySnapshot.ts, EquitySnapshot.equity)
    if source is not None:
        stmt = stmt.where(EquitySnapshot.source == source)
    rows = session.execute(stmt.order_by(EquitySnapshot.ts.asc())).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series([float(e) for _, e in rows], index=[t for t, _ in rows])


def _segment_returns(session: Session) -> pd.Series:
    """Per-period returns computed *within* each source, then concatenated.

    Deliberately never across the boundary. The replay keeps its own internal
    performance (that's the point of the accelerated dry-run) without the step
    from its closing equity to the live series' opening equity being counted as
    a gain.
    """
    parts = []
    for src in ("replay", "live"):
        eq = _equity_series(session, src)
        if len(eq) >= 2:
            parts.append(eq.pct_change().dropna())
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).sort_index()


def _compounded(returns: pd.Series) -> float:
    return float((1.0 + returns).prod() - 1.0) if len(returns) else 0.0


def _basket_return(session: Session, symbols: list[str], start: datetime) -> float | None:
    """Equal-weight buy-and-hold return of the watchlist since ``start``."""
    rets = []
    for sym in symbols:
        first = session.execute(
            select(DailyBar.close).where(DailyBar.symbol == sym, DailyBar.ts >= start)
            .order_by(DailyBar.ts.asc()).limit(1)
        ).scalar_one_or_none()
        last = session.execute(
            select(DailyBar.close).where(DailyBar.symbol == sym)
            .order_by(DailyBar.ts.desc()).limit(1)
        ).scalar_one_or_none()
        if first and last and float(first) > 0:
            rets.append(float(last) / float(first) - 1.0)
    return sum(rets) / len(rets) if rets else None


def evaluate_gate(
    session: Session, settings: Settings, watchlist_symbols: list[str]
) -> GateResult:
    state = get_state(session)
    start = state.dry_run_started_at or datetime.now(timezone.utc)
    criteria: list[Criterion] = []

    # 1. trading days
    days = trading_days_count(session)
    criteria.append(Criterion(
        "trading_days", "≥ 60 trading days on paper",
        days >= settings.golive_min_trading_days,
        f"{days} of {settings.golive_min_trading_days} days",
        days, settings.golive_min_trading_days,
    ))

    # 2. risk-adjusted excess return vs basket
    rets = _segment_returns(session)
    paper_ret = _compounded(rets)
    paper_sharpe = sharpe(rets) if len(rets) >= 2 else 0.0
    basket = _basket_return(session, watchlist_symbols, start)
    excess = (paper_ret - basket) if basket is not None else None
    passed2 = excess is not None and excess > 0 and paper_sharpe > 0
    criteria.append(Criterion(
        "excess_return", "Positive risk-adjusted excess vs basket",
        passed2,
        (f"paper {paper_ret*100:.1f}% vs basket {basket*100:.1f}% "
         f"(excess {excess*100:+.1f}%), Sharpe {paper_sharpe:.2f}")
        if excess is not None else "insufficient data",
        round(excess, 4) if excess is not None else None, 0,
    ))

    # 3. drawdown within model (backtest) expectation. Measured on a curve
    # rebuilt from the seam-free returns, not on the spliced series.
    curve = settings.starting_equity * (1.0 + rets).cumprod()
    paper_dd = max_drawdown(curve) if len(curve) >= 2 else 0.0
    bt = session.execute(
        select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    bt_dd = (bt.metrics or {}).get("max_drawdown") if bt else None
    if bt_dd is not None:
        allowed = bt_dd * settings.golive_drawdown_tolerance  # both negative
        passed3 = paper_dd >= allowed
        detail3 = f"paper {paper_dd*100:.1f}% vs allowed {allowed*100:.1f}% (backtest {bt_dd*100:.1f}%)"
    else:
        passed3, detail3 = False, "no backtest to set expectation"
    criteria.append(Criterion(
        "drawdown", "Max drawdown within model expectation", passed3, detail3,
        round(paper_dd, 4), round(bt_dd, 4) if bt_dd is not None else None,
    ))

    # 4. zero breaker malfunctions (unacknowledged firings)
    unack = breaker_event_count(session, unacknowledged_only=True)
    total_breakers = breaker_event_count(session)
    criteria.append(Criterion(
        "breakers", "Zero breaker malfunctions", unack == 0,
        f"{total_breakers} breaker events, {unack} unreviewed", unack, 0,
    ))

    # 5. manual decision reviews
    reviews = session.execute(select(func.count()).select_from(DecisionReview)).scalar_one()
    criteria.append(Criterion(
        "reviews", "≥ 20 decision logs reviewed",
        reviews >= settings.golive_min_reviews,
        f"{reviews} of {settings.golive_min_reviews} reviewed",
        reviews, settings.golive_min_reviews,
    ))

    return GateResult(
        passed=all(c.passed for c in criteria),
        dry_run_started_at=start.isoformat() if start else None,
        criteria=criteria,
    )
