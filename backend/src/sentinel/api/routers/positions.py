"""What we actually hold, and why — plus intent that hasn't filled yet.

The decision log records intent at submit time, so an OPEN row means "an order
went to the broker", not "we own this". Keeping unfilled orders visible next to
real holdings is the difference between those two readings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.deps import get_db, settings_dep, watchlist_dep
from sentinel.config import Settings, Watchlist
from sentinel.execution.factory import BrokerUnavailable, make_broker_with_status
from sentinel.models import Decision, SignalSnapshot
from sentinel.repositories import get_latest_prices
from sentinel.schemas import HeldPosition, PendingOrder, PositionsResponse

router = APIRouter(tags=["account"])


@router.get("/positions", response_model=PositionsResponse)
def positions(
    db: Session = Depends(get_db),
    wl: Watchlist = Depends(watchlist_dep),
    settings: Settings = Depends(settings_dep),
) -> PositionsResponse:
    try:
        broker, status = make_broker_with_status(settings)
    except BrokerUnavailable as exc:
        return PositionsResponse(
            mode=settings.mode, broker="alpaca", available=False, detail=str(exc)
        )

    try:
        held = broker.get_positions()
        working = broker.open_orders()
    except Exception as exc:  # noqa: BLE001 - broker/network optional at rest
        return PositionsResponse(
            mode=settings.mode, broker=status.broker, available=False, detail=str(exc)
        )

    symbols = sorted({*held, *(o.symbol for o in working)})
    quotes = get_latest_prices(db, symbols) if symbols else {}
    entries = _entry_decisions(db, list(held))
    signals = _signals(db, symbols)

    out: list[HeldPosition] = []
    for symbol, p in sorted(held.items()):
        quote = quotes.get(symbol)
        last = float(quote.price) if quote is not None else None
        cost = p.qty * p.avg_entry
        pnl = (p.market_value - cost) if p.market_value is not None else None
        entry = entries.get(symbol)
        sig = signals.get(symbol)
        sizing = (entry.sizing or {}) if entry is not None else {}
        out.append(
            HeldPosition(
                symbol=symbol,
                name=wl.name_for(symbol),
                qty=p.qty,
                avg_entry=p.avg_entry,
                last_price=last,
                market_value=p.market_value,
                unrealized_pnl=round(pnl, 2) if pnl is not None else None,
                unrealized_pnl_pct=(
                    round(pnl / cost * 100.0, 2) if pnl is not None and cost else None
                ),
                opened_at=entry.ts if entry is not None else None,
                entry_reason=entry.reason if entry is not None else None,
                entry_conviction=entry.conviction if entry is not None else None,
                entry_drivers=list(entry.drivers or []) if entry is not None else [],
                current_signal=sig.signal if sig is not None else None,
                current_conviction=sig.conviction if sig is not None else None,
                current_drivers=list(sig.drivers or []) if sig is not None else [],
                stop_price=sizing.get("stop"),
                take_profit=sizing.get("take_profit"),
            )
        )

    pending: list[PendingOrder] = []
    for o in sorted(working, key=lambda x: (x.symbol, x.side)):
        quote = quotes.get(o.symbol)
        last = float(quote.price) if quote is not None else None
        # Negative means a buy limit is under the market and cannot fill.
        gap = (
            round(o.limit_price - last, 2)
            if o.limit_price is not None and last is not None
            else None
        )
        pending.append(
            PendingOrder(
                symbol=o.symbol,
                name=wl.name_for(o.symbol),
                qty=o.qty,
                side=o.side,
                status=o.status,
                limit_price=o.limit_price,
                last_price=last,
                gap_to_fill=gap,
                submitted_at=o.submitted_at,
                reason=_pending_reason(o.side, gap),
            )
        )

    return PositionsResponse(
        mode=settings.mode,
        broker=status.broker,
        available=True,
        positions=out,
        pending=pending,
        total_market_value=round(sum(p.market_value for p in out), 2),
        total_unrealized_pnl=round(
            sum(p.unrealized_pnl or 0.0 for p in out), 2
        ),
        detail=status.detail,
    )


def _pending_reason(side: str, gap: float | None) -> str | None:
    if gap is None:
        return "waiting to fill"
    if side == "buy" and gap < 0:
        return f"limit is {abs(gap):.2f} below the market — cannot fill until it drops"
    return "waiting to fill"


def _entry_decisions(db: Session, symbols: list[str]) -> dict[str, Decision]:
    """The most recent OPEN decision per held symbol — why we're in it."""
    if not symbols:
        return {}
    rows = db.execute(
        select(Decision)
        .where(Decision.symbol.in_(symbols), Decision.action == "OPEN")
        .order_by(Decision.ts.desc(), Decision.id.desc())
    ).scalars()
    out: dict[str, Decision] = {}
    for d in rows:
        out.setdefault(d.symbol, d)  # first seen is the most recent
    return out


def _signals(db: Session, symbols: list[str]) -> dict[str, SignalSnapshot]:
    if not symbols:
        return {}
    rows = db.execute(
        select(SignalSnapshot).where(SignalSnapshot.symbol.in_(symbols))
    ).scalars()
    return {r.symbol: r for r in rows}
