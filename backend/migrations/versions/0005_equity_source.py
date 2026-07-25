"""tag equity snapshots as forward-live or historical replay

The go-live gate and the return figure both read one equity series. Until now
the accelerated replay (golive/simulate.py) and the live trading loop wrote
into it indistinguishably, so `last / first - 1` measured the *seam* between
the replay's last value and the live series' first — not a return on anything.

Revision ID: 0005_equity_source
Revises: 0004_dryrun_golive
Create Date: 2026-07-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_equity_source"
down_revision: str | None = "0004_dryrun_golive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equity_snapshots",
        sa.Column("source", sa.String(8), nullable=False, server_default="live"),
    )
    # Backfill: the replay wrote one row per trading day off a daily bar index,
    # so its timestamps land exactly on midnight UTC. The live loop stamps
    # datetime.now(), which never does. This heuristic is only sound for rows
    # written before this migration — new rows are tagged explicitly.
    op.execute(
        "UPDATE equity_snapshots SET source = 'replay' "
        "WHERE ts::time = TIME '00:00:00'"
    )
    op.create_index("ix_equity_snapshots_source", "equity_snapshots", ["source"])


def downgrade() -> None:
    op.drop_index("ix_equity_snapshots_source", table_name="equity_snapshots")
    op.drop_column("equity_snapshots", "source")
