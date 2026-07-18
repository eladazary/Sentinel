"""Random decision-log sampling and review recording (gate criterion 5)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.models import Decision, DecisionReview


def sample_unreviewed(session: Session, n: int = 20) -> list[Decision]:
    """Return up to ``n`` random not-yet-reviewed decisions for manual review."""
    reviewed = select(DecisionReview.decision_id)
    stmt = (
        select(Decision)
        .where(Decision.id.notin_(reviewed))
        .order_by(func.random())
        .limit(n)
    )
    return list(session.execute(stmt).scalars())


def record_review(
    session: Session, decision_id: int, ok: bool = True, note: str | None = None
) -> None:
    stmt = insert(DecisionReview).values(
        decision_id=decision_id, reviewed_at=datetime.now(timezone.utc), ok=ok, note=note
    ).on_conflict_do_update(
        index_elements=[DecisionReview.decision_id],
        set_={"reviewed_at": datetime.now(timezone.utc), "ok": ok, "note": note},
    )
    session.execute(stmt)


def review_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(DecisionReview)).scalar_one()
