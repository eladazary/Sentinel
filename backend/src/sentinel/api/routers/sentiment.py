"""News feed and tracker (sentiment desk) management."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db
from sentinel.repositories import recent_news
from sentinel.schemas import NewsOut, TrackerOut
from sentinel.social import tracker

router = APIRouter(tags=["sentiment"])


@router.get("/news", response_model=list[NewsOut])
def news(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    symbol: str | None = None,
) -> list[NewsOut]:
    rows = recent_news(db, limit=limit, symbol=symbol.upper() if symbol else None)
    return [
        NewsOut(
            id=n.id, symbol=n.symbol, ts=n.ts, source=n.source, headline=n.headline,
            url=n.url, event_type=n.event_type, materiality=n.materiality,
            sentiment_score=n.sentiment_score, impact=n.impact,
        )
        for n in rows
    ]


def _to_tracker(a) -> TrackerOut:
    return TrackerOut(
        handle=a.handle, source=a.source, credibility=a.credibility, hit_rate=a.hit_rate,
        n_calls=a.n_calls, n_scored=a.n_scored, pinned=a.pinned, note=a.note,
        last_seen=a.last_seen,
    )


@router.get("/trackers", response_model=list[TrackerOut])
def list_trackers(db: Session = Depends(get_db)) -> list[TrackerOut]:
    return [_to_tracker(a) for a in tracker.list_accounts(db)]


@router.post("/trackers", response_model=list[TrackerOut])
def add_tracker(
    db: Session = Depends(get_db),
    handle: str = Body(embed=True),
    source: str = Body(embed=True),
    pinned: bool = Body(default=False, embed=True),
) -> list[TrackerOut]:
    tracker.add_account(db, handle.strip().lstrip("@"), source.strip().lower(), pinned)
    db.commit()
    return [_to_tracker(a) for a in tracker.list_accounts(db)]


@router.delete("/trackers/{source}/{handle}", response_model=list[TrackerOut])
def remove_tracker(
    source: str, handle: str, db: Session = Depends(get_db)
) -> list[TrackerOut]:
    tracker.remove_account(db, handle, source)
    db.commit()
    return [_to_tracker(a) for a in tracker.list_accounts(db)]


@router.put("/trackers/{source}/{handle}/pin", response_model=list[TrackerOut])
def pin_tracker(
    source: str, handle: str, pinned: bool = Body(embed=True), db: Session = Depends(get_db)
) -> list[TrackerOut]:
    tracker.set_pinned(db, handle, source, pinned)
    db.commit()
    return [_to_tracker(a) for a in tracker.list_accounts(db)]
