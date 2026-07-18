"""Hard risk limits — enforced in code, not configurable away (spec §6).

* Daily loss circuit breaker: −3% of equity → go flat, halt for the day, notify.
* Max drawdown breaker: −12% from high-water mark → lock system to DRY_RUN.
* Per-order sanity checks: price collar, max share/notional, duplicate guard.

These are independent of the Risk Factor dial.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BreakerResult:
    daily_loss_tripped: bool
    drawdown_tripped: bool
    day_pnl_pct: float
    drawdown_pct: float
    actions: list[str] = field(default_factory=list)

    @property
    def any_tripped(self) -> bool:
        return self.daily_loss_tripped or self.drawdown_tripped


def check_breakers(
    *,
    equity: float,
    day_start_equity: float,
    high_water_mark: float,
    daily_loss_pct: float = 3.0,
    max_drawdown_pct: float = 12.0,
) -> BreakerResult:
    """Evaluate the hard breakers against the current account state."""
    day_pnl_pct = (
        (equity - day_start_equity) / day_start_equity * 100.0
        if day_start_equity > 0
        else 0.0
    )
    hwm = max(high_water_mark, equity)
    drawdown_pct = (equity - hwm) / hwm * 100.0 if hwm > 0 else 0.0

    daily_loss_tripped = day_pnl_pct <= -abs(daily_loss_pct)
    drawdown_tripped = drawdown_pct <= -abs(max_drawdown_pct)

    actions: list[str] = []
    if daily_loss_tripped:
        actions.append("flatten_all")
        actions.append("halt_for_day")
        actions.append("notify")
    if drawdown_tripped:
        actions.append("lock_to_dry_run")
        actions.append("notify")

    return BreakerResult(
        daily_loss_tripped=daily_loss_tripped,
        drawdown_tripped=drawdown_tripped,
        day_pnl_pct=round(day_pnl_pct, 4),
        drawdown_pct=round(drawdown_pct, 4),
        actions=actions,
    )


@dataclass
class OrderCheck:
    ok: bool
    reason: str


def validate_order(
    *,
    symbol: str,
    side: str,
    qty: int,
    limit_price: float,
    last_price: float,
    equity: float,
    pending_keys: set[tuple[str, str]],
    price_collar_pct: float = 5.0,
    max_position_notional_pct: float = 25.0,
) -> OrderCheck:
    """Per-order sanity checks before anything is submitted to a broker."""
    if qty <= 0:
        return OrderCheck(False, "non-positive quantity")
    if limit_price <= 0 or last_price <= 0:
        return OrderCheck(False, "invalid price")

    # Duplicate-order guard: one working order per (symbol, side).
    if (symbol, side) in pending_keys:
        return OrderCheck(False, f"duplicate working order for {symbol} {side}")

    # Price collar: limit must be within N% of the last trade.
    collar = abs(limit_price - last_price) / last_price * 100.0
    if collar > price_collar_pct:
        return OrderCheck(
            False, f"limit {limit_price} is {collar:.1f}% off last (> {price_collar_pct}%)"
        )

    # Max notional sanity (a hard ceiling above any dial-driven size).
    notional = qty * limit_price
    if equity > 0 and notional > equity * max_position_notional_pct / 100.0:
        return OrderCheck(
            False,
            f"notional {notional:.0f} exceeds {max_position_notional_pct}% of equity",
        )
    return OrderCheck(True, "ok")
