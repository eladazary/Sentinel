"""phase 1: signals, decision log, equity snapshots, backtest runs

Revision ID: 0002_signals_execution
Revises: 0001_initial
Create Date: 2026-07-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_signals_execution"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_snapshots",
        sa.Column("symbol", sa.String(16), primary_key=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conviction", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("technical_score", sa.Float(), nullable=True),
        sa.Column("news_score", sa.Float(), nullable=True),
        sa.Column("social_score", sa.Float(), nullable=True),
        sa.Column("signal", sa.String(8), nullable=False),
        sa.Column("drivers", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=True),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("signal", sa.String(8), nullable=False),
        sa.Column("conviction", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("risk_factor", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("drivers", sa.JSON(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("sizing", sa.JSON(), nullable=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_decisions_ts_desc", "decisions", [sa.text("ts DESC")])
    op.create_index("ix_decisions_symbol", "decisions", ["symbol"])

    op.create_table(
        "equity_snapshots",
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("equity", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash", sa.Numeric(18, 4), nullable=False),
        sa.Column("exposure_pct", sa.Float(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("risk_factor", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.String(32), nullable=True),
        sa.Column("end_date", sa.String(32), nullable=True),
        sa.Column("n_trades", sa.Integer(), nullable=False),
        sa.Column("wf_auc", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("benchmarks", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("equity_snapshots")
    op.drop_index("ix_decisions_symbol", table_name="decisions")
    op.drop_index("ix_decisions_ts_desc", table_name="decisions")
    op.drop_table("decisions")
    op.drop_table("signal_snapshots")
