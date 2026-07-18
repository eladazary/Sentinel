"""Phase 3 API: go-live gate, mode switch, breaker acks, decision review."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep, watchlist_dep
from sentinel.config import Settings, Watchlist
from sentinel.golive.gate import evaluate_gate
from sentinel.golive.mode import lock_dry_run, unlock_live
from sentinel.golive.review import record_review, sample_unreviewed
from sentinel.models import BreakerEvent
from sentinel.schemas import (
    BreakerOut,
    GateOut,
    ModeOut,
    SampleDecisionOut,
    UnlockOut,
)
from sentinel.system_state import get_state, in_cooloff

router = APIRouter(tags=["golive"])


@router.get("/golive", response_model=GateOut)
def golive(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    wl: Watchlist = Depends(watchlist_dep),
) -> GateOut:
    result = evaluate_gate(db, settings, wl.symbols)
    db.commit()  # gate touches system_state (creates row / starts clock)
    return GateOut(**result.as_dict())


@router.get("/mode", response_model=ModeOut)
def mode(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> ModeOut:
    s = get_state(db)
    cooloff = in_cooloff(db, settings.live_cooloff_hours)
    db.commit()
    return ModeOut(
        mode=s.mode, dry_run_started_at=s.dry_run_started_at,
        last_breaker_at=s.last_breaker_at, live_unlocked_at=s.live_unlocked_at,
        live_capital_cap=s.live_capital_cap, in_cooloff=cooloff,
    )


@router.post("/mode/unlock-live", response_model=UnlockOut)
def unlock(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    wl: Watchlist = Depends(watchlist_dep),
    confirmation: str = Body(embed=True),
) -> UnlockOut:
    res = unlock_live(db, settings, wl.symbols, confirmation)
    db.commit()
    return UnlockOut(ok=res.ok, mode=res.mode, reason=res.reason,
                     live_capital_cap=res.live_capital_cap)


@router.post("/mode/lock", response_model=UnlockOut)
def lock(db: Session = Depends(get_db)) -> UnlockOut:
    res = lock_dry_run(db)
    db.commit()
    return UnlockOut(ok=res.ok, mode=res.mode, reason=res.reason)


@router.get("/breakers", response_model=list[BreakerOut])
def breakers(db: Session = Depends(get_db)) -> list[BreakerOut]:
    rows = db.execute(
        select(BreakerEvent).order_by(BreakerEvent.ts.desc()).limit(50)
    ).scalars()
    return [
        BreakerOut(
            id=b.id, ts=b.ts, kind=b.kind, detail=b.detail,
            day_pnl_pct=b.day_pnl_pct, drawdown_pct=b.drawdown_pct,
            acknowledged=b.acknowledged,
        )
        for b in rows
    ]


@router.post("/breakers/{event_id}/ack", response_model=BreakerOut)
def ack_breaker(event_id: int, db: Session = Depends(get_db)) -> BreakerOut:
    b = db.get(BreakerEvent, event_id)
    if b:
        b.acknowledged = True
        db.commit()
    return BreakerOut(
        id=b.id, ts=b.ts, kind=b.kind, detail=b.detail, day_pnl_pct=b.day_pnl_pct,
        drawdown_pct=b.drawdown_pct, acknowledged=b.acknowledged,
    )


@router.get("/decisions/sample", response_model=list[SampleDecisionOut])
def sample(db: Session = Depends(get_db), n: int = 20) -> list[SampleDecisionOut]:
    rows = sample_unreviewed(db, n=n)
    return [
        SampleDecisionOut(
            id=d.id, ts=d.ts, symbol=d.symbol, action=d.action, signal=d.signal,
            conviction=d.conviction, reason=d.reason, drivers=list(d.drivers or []),
        )
        for d in rows
    ]


@router.post("/decisions/{decision_id}/review")
def review(
    decision_id: int,
    db: Session = Depends(get_db),
    ok: bool = Body(default=True, embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    record_review(db, decision_id, ok=ok, note=note)
    db.commit()
    return {"ok": True, "decision_id": decision_id}
