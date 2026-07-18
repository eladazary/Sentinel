"""SQLAlchemy ORM models.

Phase 0 stores two things:

* ``daily_bars`` — adjusted daily OHLCV, the backtesting foundation. Backed by a
  TimescaleDB hypertable partitioned on ``ts`` (see the initial migration).
* ``latest_prices`` — the most recent observed price per ticker, updated by the
  ingestion worker and read by the watchlist API for "live" display.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DailyBar(Base):
    """One adjusted daily OHLCV bar for one ticker.

    Composite primary key (symbol, ts); ``ts`` is the partitioning column for the
    TimescaleDB hypertable, so it must be part of every unique constraint.
    """

    __tablename__ = "daily_bars"
    __table_args__ = (PrimaryKeyConstraint("symbol", "ts", name="pk_daily_bars"),)

    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trade_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vwap: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)


class LatestPrice(Base):
    """The most recent observed price for a ticker (one row per symbol)."""

    __tablename__ = "latest_prices"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="alpaca")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SignalSnapshot(Base):
    """Latest signal-engine output per ticker (one row per symbol), for the UI."""

    __tablename__ = "signal_snapshots"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    social_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal: Mapped[str] = mapped_column(String(8), nullable=False)
    drivers: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Decision(Base):
    """Immutable decision log — every action AND every skip, with its reasoning
    and a feature snapshot (spec §7). Append-only; never updated."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)  # OPEN/SKIP/SELL/...
    signal: Mapped[str] = mapped_column(String(8), nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    risk_factor: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    drivers: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    features: Mapped[Any] = mapped_column(JSON, nullable=True)
    sizing: Mapped[Any] = mapped_column(JSON, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EquitySnapshot(Base):
    """Account equity over time (for the equity-curve view)."""

    __tablename__ = "equity_snapshots"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    equity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    exposure_pct: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class BacktestRun(Base):
    """A stored walk-forward backtest run and its results."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    risk_factor: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wf_auc: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[Any] = mapped_column(JSON, nullable=False)
    benchmarks: Mapped[Any] = mapped_column(JSON, nullable=False)
    config: Mapped[Any] = mapped_column(JSON, nullable=False)
