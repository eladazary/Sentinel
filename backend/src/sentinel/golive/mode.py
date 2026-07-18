"""Guarded LIVE-mode unlock (spec §2 two-step unlock, §9, §10.5).

Flipping to LIVE requires ALL of:
  * the go-live gate passes,
  * a typed confirmation phrase,
  * no breaker event within the cool-off window (24h default).

On unlock, LIVE runs capped at ``live_capital_cap``. This never happens
automatically — an operator must call it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from sentinel.config import Settings
from sentinel.golive.gate import evaluate_gate
from sentinel.system_state import get_state, in_cooloff

CONFIRM_PHRASE = "GO LIVE"


@dataclass
class UnlockResult:
    ok: bool
    mode: str
    reason: str
    live_capital_cap: float | None = None


def unlock_live(
    session: Session,
    settings: Settings,
    watchlist_symbols: list[str],
    confirmation: str,
    *,
    now: datetime | None = None,
) -> UnlockResult:
    now = now or datetime.now(timezone.utc)
    state = get_state(session)

    if confirmation.strip() != CONFIRM_PHRASE:
        return UnlockResult(False, state.mode, f'typed confirmation must be "{CONFIRM_PHRASE}"')

    if in_cooloff(session, settings.live_cooloff_hours, now):
        return UnlockResult(
            False, state.mode,
            f"cool-off active: a breaker fired within {settings.live_cooloff_hours}h",
        )

    gate = evaluate_gate(session, settings, watchlist_symbols)
    if not gate.passed:
        failed = [c.label for c in gate.criteria if not c.passed]
        return UnlockResult(False, state.mode, f"go-live gate not passed: {', '.join(failed)}")

    state.mode = "LIVE"
    state.live_unlocked_at = now
    state.live_capital_cap = settings.live_capital_cap
    state.updated_at = now
    return UnlockResult(True, "LIVE", "unlocked (capped)", settings.live_capital_cap)


def lock_dry_run(session: Session, *, now: datetime | None = None) -> UnlockResult:
    now = now or datetime.now(timezone.utc)
    state = get_state(session)
    state.mode = "DRY_RUN"
    state.updated_at = now
    return UnlockResult(True, "DRY_RUN", "reverted to dry-run")
