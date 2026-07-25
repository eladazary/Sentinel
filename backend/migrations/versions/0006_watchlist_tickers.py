"""make the watchlist universe editable at runtime

The universe lived in config/watchlist.yaml, which is baked into the image — so
anything added from the UI would vanish on the next rebuild. This table becomes
the source of truth; the YAML is used once, to seed it.

Revision ID: 0006_watchlist_tickers
Revises: 0005_equity_source
Create Date: 2026-07-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_watchlist_tickers"
down_revision: str | None = "0005_equity_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_tickers",
        sa.Column("symbol", sa.String(16), primary_key=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("sector_etf", sa.String(16), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("backfilled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("watchlist_tickers")
