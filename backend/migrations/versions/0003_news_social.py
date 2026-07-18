"""phase 2: news items, tracked accounts, tracker calls, earnings

Revision ID: 0003_news_social
Revises: 0002_signals_execution
Create Date: 2026-07-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_news_social"
down_revision: str | None = "0002_signals_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(128), nullable=False, unique=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=True),
        sa.Column("materiality", sa.Integer(), nullable=True),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("impact", sa.Float(), nullable=True),
    )
    op.create_index("ix_news_symbol_ts", "news_items", ["symbol", sa.text("ts DESC")])

    op.create_table(
        "sentiment_cache",
        sa.Column("symbol", sa.String(16), primary_key=True, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("news_score", sa.Float(), nullable=True),
        sa.Column("news_confidence", sa.Float(), nullable=True),
        sa.Column("news_drivers", sa.JSON(), nullable=False),
        sa.Column("social_score", sa.Float(), nullable=True),
        sa.Column("social_confidence", sa.Float(), nullable=True),
        sa.Column("social_crowding", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("social_drivers", sa.JSON(), nullable=False),
    )

    op.create_table(
        "tracked_accounts",
        sa.Column("handle", sa.String(64), primary_key=True, nullable=False),
        sa.Column("source", sa.String(16), primary_key=True, nullable=False),
        sa.Column("credibility", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("hit_rate", sa.Float(), nullable=True),
        sa.Column("n_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "tracker_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stance", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("price_at_call", sa.Float(), nullable=True),
        sa.Column("ret_5d", sa.Float(), nullable=True),
        sa.Column("ret_20d", sa.Float(), nullable=True),
        sa.Column("hit_5d", sa.Boolean(), nullable=True),
        sa.Column("hit_20d", sa.Boolean(), nullable=True),
        sa.Column("scored_20d", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_tracker_calls_acct", "tracker_calls", ["handle", "source"])

    op.create_table(
        "earnings_events",
        sa.Column("symbol", sa.String(16), primary_key=True, nullable=False),
        sa.Column("earnings_date", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("eps_estimate", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("earnings_events")
    op.drop_index("ix_tracker_calls_acct", table_name="tracker_calls")
    op.drop_table("tracker_calls")
    op.drop_table("tracked_accounts")
    op.drop_table("sentiment_cache")
    op.drop_index("ix_news_symbol_ts", table_name="news_items")
    op.drop_table("news_items")
