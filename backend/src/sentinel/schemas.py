"""Pydantic response schemas for the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthComponent(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    mode: str
    version: str
    components: list[HealthComponent]


class WatchlistTicker(BaseModel):
    symbol: str
    name: str
    price: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    as_of: datetime | None
    stale: bool
    spark: list[float]
    # Signal-engine overlay (null until the model has run).
    conviction: float | None = None
    confidence: float | None = None
    technical_score: float | None = None
    news_score: float | None = None
    social_score: float | None = None
    crowding: bool = False
    signal: str | None = None
    drivers: list[str] = []


class WatchlistResponse(BaseModel):
    mode: str
    count: int
    tickers: list[WatchlistTicker]
    max_tickers: int | None = None


class RiskProfileOut(BaseModel):
    risk_factor: int
    max_position_pct: float
    max_exposure_pct: float
    min_conviction: float
    stop_atr_mult: float
    max_new_positions_per_day: int
    trade_around_earnings: str


class RiskProfilesResponse(BaseModel):
    default_risk_factor: int
    profiles: list[RiskProfileOut]


class SignalOut(BaseModel):
    symbol: str
    ts: datetime
    conviction: float
    confidence: float
    technical_score: float | None
    signal: str
    drivers: list[str]
    model_version: str | None


class DecisionOut(BaseModel):
    id: int
    ts: datetime
    symbol: str
    action: str
    signal: str
    conviction: float
    confidence: float
    risk_factor: int
    mode: str
    reason: str
    drivers: list[str]
    broker_order_id: str | None


class PositionOut(BaseModel):
    symbol: str
    qty: int
    avg_entry: float
    market_value: float


class AccountResponse(BaseModel):
    available: bool
    mode: str
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    exposure_pct: float | None = None
    positions: list[PositionOut] = []
    detail: str | None = None
    # Where the numbers came from: the broker itself, or the equity ledger the
    # worker writes each cycle. "none" means neither could be read.
    source: str = "broker"
    broker: str | None = None
    degraded: bool = False
    as_of: datetime | None = None


class NewsOut(BaseModel):
    id: int
    symbol: str
    ts: datetime
    source: str
    headline: str
    url: str | None
    event_type: str | None
    materiality: int | None
    sentiment_score: float | None
    impact: float | None


class TrackerOut(BaseModel):
    handle: str
    source: str
    credibility: float
    hit_rate: float | None
    n_calls: int
    n_scored: int
    pinned: bool
    note: str | None
    last_seen: datetime | None


class PerformancePoint(BaseModel):
    ts: datetime
    equity: float
    drawdown_pct: float
    spy: float | None


class PerformanceSummary(BaseModel):
    """The bottom line: what the money is now and what it earned.

    Everything here describes the *forward* paper run only. The accelerated
    historical replay is reported separately as ``replay_return_pct`` and is
    never folded into the headline yield — it's a backtest, not money.
    """

    equity: float
    starting_equity: float
    pnl: float
    return_pct: float
    max_drawdown_pct: float
    benchmark_return_pct: float | None = None
    replay_return_pct: float | None = None
    positions_opened: int
    as_of: datetime | None = None
    # Set when nothing has traded, so the UI can explain a flat 0.00%.
    note: str | None = None


class PerformanceResponse(BaseModel):
    starting_equity: float
    points: list[PerformancePoint]
    summary: PerformanceSummary | None = None


class CriterionOut(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str
    value: float | int | None
    threshold: float | int | None


class GateOut(BaseModel):
    passed: bool
    dry_run_started_at: str | None
    criteria: list[CriterionOut]


class ModeOut(BaseModel):
    mode: str
    dry_run_started_at: datetime | None
    last_breaker_at: datetime | None
    live_unlocked_at: datetime | None
    live_capital_cap: float | None
    in_cooloff: bool


class UnlockOut(BaseModel):
    ok: bool
    mode: str
    reason: str
    live_capital_cap: float | None = None


class BreakerOut(BaseModel):
    id: int
    ts: datetime
    kind: str
    detail: str
    day_pnl_pct: float | None
    drawdown_pct: float | None
    acknowledged: bool


class SampleDecisionOut(BaseModel):
    id: int
    ts: datetime
    symbol: str
    action: str
    signal: str
    conviction: float
    reason: str
    drivers: list[str]


class BacktestOut(BaseModel):
    id: int
    created_at: datetime
    risk_factor: int
    start_date: str | None
    end_date: str | None
    n_trades: int
    wf_auc: float | None
    metrics: dict
    benchmarks: dict
    config: dict
