"""phase 3: system state, breaker events, decision reviews

Revision ID: 0004_dryrun_golive
Revises: 0003_news_social
Create Date: 2026-07-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_dryrun_golive"
down_revision: str | None = "0003_news_social"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("mode", sa.String(8), nullable=False, server_default="DRY_RUN"),
        sa.Column("dry_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_breaker_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_capital_cap", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "breaker_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("day_pnl_pct", sa.Float(), nullable=True),
        sa.Column("drawdown_pct", sa.Float(), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_breaker_events_ts", "breaker_events", [sa.text("ts DESC")])

    op.create_table(
        "decision_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("decision_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("decision_reviews")
    op.drop_index("ix_breaker_events_ts", table_name="breaker_events")
    op.drop_table("breaker_events")
    op.drop_table("system_state")
