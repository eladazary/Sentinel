"""SQLAlchemy ORM models.

Phase 0 stores two things:

* ``daily_bars`` — adjusted daily OHLCV, the backtesting foundation. Backed by a
  TimescaleDB hypertable partitioned on ``ts`` (see the initial migration).
* ``latest_prices`` — the most recent observed price per ticker, updated by the
  ingestion worker and read by the watchlist API for "live" display.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Numeric, PrimaryKeyConstraint, String
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
