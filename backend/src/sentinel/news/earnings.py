"""Earnings calendar (Finnhub) + the blackout-window query used by the risk layer.

The risk manager tightens around earnings (spec §6): within the blackout window,
new entries are blocked (risk 1–3), reduced (4–7), or allowed (8–10) per the
Risk Profile's ``trade_around_earnings``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.logging_config import get_logger
from sentinel.models import EarningsEvent

log = get_logger(__name__)

_URL = "https://finnhub.io/api/v1/calendar/earnings"


def fetch_finnhub_earnings(
    symbol: str, api_key: str, *, ahead_days: int = 90
) -> list[tuple[datetime, float | None]]:
    now = datetime.now(timezone.utc)
    params = {
        "symbol": symbol.upper(),
        "from": now.date().isoformat(),
        "to": (now + timedelta(days=ahead_days)).date().isoformat(),
        "token": api_key,
    }
    try:
        with httpx.Client() as client:
            r = client.get(_URL, params=params, timeout=15)
            r.raise_for_status()
            rows = r.json().get("earningsCalendar", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("Finnhub earnings fetch failed for %s: %s", symbol, exc)
        return []
    out: list[tuple[datetime, float | None]] = []
    for row in rows:
        try:
            d = datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append((d, row.get("epsEstimate")))
    return out


def upsert_earnings(
    session: Session, symbol: str, earnings_date: datetime, eps: float | None
) -> None:
    stmt = insert(EarningsEvent).values(
        symbol=symbol.upper(), earnings_date=earnings_date, eps_estimate=eps,
        updated_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[EarningsEvent.symbol, EarningsEvent.earnings_date],
        set_={"eps_estimate": stmt.excluded.eps_estimate, "updated_at": stmt.excluded.updated_at},
    )
    session.execute(stmt)


def next_earnings(session: Session, symbol: str, now: datetime) -> datetime | None:
    return session.execute(
        select(EarningsEvent.earnings_date)
        .where(EarningsEvent.symbol == symbol.upper(), EarningsEvent.earnings_date >= now)
        .order_by(EarningsEvent.earnings_date.asc())
        .limit(1)
    ).scalar_one_or_none()


def is_in_blackout(session: Session, symbol: str, now: datetime, hours: int) -> bool:
    nxt = next_earnings(session, symbol, now)
    if nxt is None:
        return False
    return nxt <= now + timedelta(hours=hours)
