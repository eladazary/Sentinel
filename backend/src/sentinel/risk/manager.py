"""Position sizing and stop/target placement from the Risk Profile.

Long-only in v1 (spec §2). Sizing is a function of equity and the Risk Factor's
percentage caps; stops are ATR-multiple based; brackets carry a take-profit so
protection lives at the broker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel.risk.profile import RiskProfile


@dataclass
class SizingDecision:
    allowed: bool
    shares: int
    notional: float
    stop_price: float | None
    take_profit: float | None
    reason: str

    @property
    def blocked(self) -> bool:
        return not self.allowed or self.shares <= 0


def size_position(
    *,
    equity: float,
    price: float,
    atr: float,
    profile: RiskProfile,
    current_exposure_value: float,
    reward_risk: float = 2.0,
) -> SizingDecision:
    """Size a new long position under the risk caps.

    Respects both the per-position cap and the remaining total-exposure headroom;
    places an ATR stop and a reward:risk take-profit for a bracket order.
    """
    if price <= 0:
        return SizingDecision(False, 0, 0.0, None, None, "invalid price")
    if atr <= 0:
        return SizingDecision(False, 0, 0.0, None, None, "invalid ATR")

    per_pos_cap = equity * profile.max_position_pct / 100.0
    exposure_cap = equity * profile.max_exposure_pct / 100.0
    headroom = exposure_cap - current_exposure_value
    if headroom <= 0:
        return SizingDecision(False, 0, 0.0, None, None, "total exposure cap reached")

    target_value = min(per_pos_cap, headroom)
    shares = int(math.floor(target_value / price))
    if shares <= 0:
        return SizingDecision(
            False, 0, 0.0, None, None, "position cap too small for one share"
        )

    stop_distance = profile.stop_atr_mult * atr
    stop_price = round(price - stop_distance, 2)
    take_profit = round(price + reward_risk * stop_distance, 2)
    notional = round(shares * price, 2)
    return SizingDecision(
        allowed=True,
        shares=shares,
        notional=notional,
        stop_price=stop_price,
        take_profit=take_profit,
        reason="ok",
    )
