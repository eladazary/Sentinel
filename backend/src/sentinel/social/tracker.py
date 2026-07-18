"""Tracker ledger (spec §4.3).

Every tracked account earns credibility from *measured accuracy*: when it posts a
directional call on a watchlist ticker we log it, then score the outcome at +5 and
+20 trading days. Credibility is a Bayesian-shrunk hit rate, so loud accounts with
no track record sit at a neutral 0.5 rather than getting undue weight.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.logging_config import get_logger
from sentinel.models import DailyBar, TrackedAccount, TrackerCall

log = get_logger(__name__)

# Beta(2,2) prior → credibility starts at 0.5 and needs evidence to move.
_PRIOR_A = 2.0
_PRIOR_B = 2.0


def credibility_from_stats(n_scored: int, hits: float) -> float:
    """Bayesian-shrunk hit rate in [0, 1]. 0.5 with no scored calls."""
    return round((hits + _PRIOR_A) / (n_scored + _PRIOR_A + _PRIOR_B), 4)


def upsert_account(
    session: Session, handle: str, source: str, *, note: str | None = None,
    ts: datetime | None = None,
) -> None:
    stmt = insert(TrackedAccount).values(
        handle=handle, source=source, credibility=0.5, note=note,
        last_seen=ts or datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TrackedAccount.handle, TrackedAccount.source],
        set_={"note": stmt.excluded.note, "last_seen": stmt.excluded.last_seen},
    )
    session.execute(stmt)


def record_call(
    session: Session, *, handle: str, source: str, symbol: str, ts: datetime,
    stance: float, text: str | None, price_at_call: float | None,
) -> None:
    """Log a directional call and bump the account's call count."""
    session.add(
        TrackerCall(
            handle=handle, source=source, symbol=symbol, ts=ts, stance=stance,
            text=(text or "")[:1000], price_at_call=price_at_call,
        )
    )
    upsert_account(session, handle, source, note=(text or "")[:200], ts=ts)
    acct = session.get(TrackedAccount, (handle, source))
    if acct:
        acct.n_calls += 1


def credibility_map(session: Session) -> dict[tuple[str, str], float]:
    rows = session.execute(
        select(TrackedAccount).where(TrackedAccount.active.is_(True))
    ).scalars()
    return {(a.handle, a.source): a.credibility for a in rows}


def _nth_forward_close(
    session: Session, symbol: str, after: datetime, n: int
) -> float | None:
    """Close of the n-th daily bar strictly after ``after`` (None if not enough)."""
    rows = session.execute(
        select(DailyBar.close)
        .where(DailyBar.symbol == symbol, DailyBar.ts > after)
        .order_by(DailyBar.ts.asc())
        .limit(n)
    ).scalars().all()
    return float(rows[n - 1]) if len(rows) >= n else None


def score_pending_calls(session: Session, now: datetime | None = None) -> int:
    """Score matured calls at +5 and +20 trading days; refresh credibility.

    Returns the number of calls fully scored (20-day) this pass.
    """
    now = now or datetime.now(timezone.utc)
    pending = session.execute(
        select(TrackerCall).where(TrackerCall.scored_20d.is_(False))
    ).scalars().all()

    touched_accounts: set[tuple[str, str]] = set()
    fully_scored = 0
    for call in pending:
        base = call.price_at_call
        if base is None:
            base = _price_at_or_before(session, call.symbol, call.ts)
            call.price_at_call = base
        if not base:
            continue
        if call.hit_5d is None:
            c5 = _nth_forward_close(session, call.symbol, call.ts, 5)
            if c5 is not None:
                call.ret_5d = c5 / base - 1.0
                call.hit_5d = (call.ret_5d > 0) == (call.stance > 0)
        c20 = _nth_forward_close(session, call.symbol, call.ts, 20)
        if c20 is not None:
            call.ret_20d = c20 / base - 1.0
            call.hit_20d = (call.ret_20d > 0) == (call.stance > 0)
            call.scored_20d = True
            fully_scored += 1
            touched_accounts.add((call.handle, call.source))

    for handle, source in touched_accounts:
        _refresh_account(session, handle, source)
    return fully_scored


def _price_at_or_before(session: Session, symbol: str, ts: datetime) -> float | None:
    val = session.execute(
        select(DailyBar.close)
        .where(DailyBar.symbol == symbol, DailyBar.ts <= ts)
        .order_by(DailyBar.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(val) if val is not None else None


def _refresh_account(session: Session, handle: str, source: str) -> None:
    scored = session.execute(
        select(TrackerCall).where(
            TrackerCall.handle == handle,
            TrackerCall.source == source,
            TrackerCall.scored_20d.is_(True),
        )
    ).scalars().all()
    n = len(scored)
    hits = sum(1 for c in scored if c.hit_20d)
    acct = session.get(TrackedAccount, (handle, source))
    if acct:
        acct.n_scored = n
        acct.hit_rate = round(hits / n, 4) if n else None
        acct.credibility = credibility_from_stats(n, hits)


# --- management (API) ---

def add_account(session: Session, handle: str, source: str, pinned: bool = False) -> None:
    stmt = insert(TrackedAccount).values(
        handle=handle, source=source, credibility=0.5, pinned=pinned, active=True
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TrackedAccount.handle, TrackedAccount.source],
        set_={"active": True, "pinned": pinned},
    )
    session.execute(stmt)


def remove_account(session: Session, handle: str, source: str) -> None:
    acct = session.get(TrackedAccount, (handle, source))
    if acct:
        acct.active = False


def set_pinned(session: Session, handle: str, source: str, pinned: bool) -> None:
    acct = session.get(TrackedAccount, (handle, source))
    if acct:
        acct.pinned = pinned


def list_accounts(session: Session, include_inactive: bool = False) -> list[TrackedAccount]:
    stmt = select(TrackedAccount)
    if not include_inactive:
        stmt = stmt.where(TrackedAccount.active.is_(True))
    stmt = stmt.order_by(TrackedAccount.pinned.desc(), TrackedAccount.credibility.desc())
    return list(session.execute(stmt).scalars())
