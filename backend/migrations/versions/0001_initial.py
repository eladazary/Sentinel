"""initial schema: daily_bars hypertable + latest_prices

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TimescaleDB provides the hypertable machinery. IF NOT EXISTS keeps this
    # safe on the timescaledb image (extension is preloaded) and no-ops
    # gracefully if it is somehow already present.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "daily_bars",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("trade_count", sa.BigInteger(), nullable=True),
        sa.Column("vwap", sa.Numeric(18, 6), nullable=True),
        sa.PrimaryKeyConstraint("symbol", "ts", name="pk_daily_bars"),
    )
    # Convert to a hypertable partitioned on ts. migrate_data=true handles the
    # (empty at creation) table cleanly.
    op.execute(
        "SELECT create_hypertable('daily_bars', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    # Common query pattern: recent bars for one symbol, newest first.
    op.create_index(
        "ix_daily_bars_symbol_ts_desc",
        "daily_bars",
        ["symbol", sa.text("ts DESC")],
    )

    op.create_table(
        "latest_prices",
        sa.Column("symbol", sa.String(length=16), primary_key=True, nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("latest_prices")
    op.drop_index("ix_daily_bars_symbol_ts_desc", table_name="daily_bars")
    op.drop_table("daily_bars")
