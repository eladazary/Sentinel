"""Risk Factor (1–10) → concrete, code-enforced parameters (spec §6).

The spec anchors each parameter at Risk 1 / 5 / 10 and says intermediate values
"interpolate linearly". Those three anchors are NOT collinear (e.g. max exposure
30/70/95), so we interpolate **piecewise-linearly through the anchors** — this
reproduces every anchor exactly, which the prototype UI's endpoint-only linear
approximation does not. The API exposes this so the Risk Dial previews exactly
what the engine will do.

DEVIATION FROM SPEC §6 — the conviction gate. The spec anchors it at 70/50/35,
but conviction is ``(p - 0.5) * 200`` over the technical model's probability, so
those gates demand p = 0.85 / 0.75 / 0.675. A daily-bar swing classifier does
not produce those: across the first 937 logged decisions conviction ranged
[-19.9, +25.0] with a median of -5.2 and p90 of 13.8, so *every* decision was
skipped and the system never opened a position. The gate is re-anchored below to
that measured distribution — see CONVICTION_GATE_ANCHORS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


# Conviction gate at Risk 1 / 5 / 10, expressed against what the model can
# actually emit. In probability terms: 0.60 / 0.56 / 0.525 — a conservative,
# moderate, and permissive read of a weak-but-real daily edge. Re-derive these if
# the model is retrained or recalibrated; a gate above ~25 disables trading
# entirely, which is the bug this replaces.
CONVICTION_GATE_ANCHORS = (20.0, 12.0, 5.0)


def _piecewise(r: float, lo: float, mid: float, hi: float) -> float:
    """Interpolate through anchors at r=1 (lo), r=5 (mid), r=10 (hi)."""
    r = max(1.0, min(10.0, float(r)))
    if r <= 5.0:
        return lo + (mid - lo) * (r - 1.0) / 4.0
    return mid + (hi - mid) * (r - 5.0) / 5.0


@dataclass(frozen=True)
class RiskProfile:
    risk_factor: int
    max_position_pct: float  # % of equity per position
    max_exposure_pct: float  # % of equity total
    min_conviction: float  # conviction gate to open
    stop_atr_mult: float  # stop distance in ATR multiples
    max_new_positions_per_day: int
    trade_around_earnings: str  # never | reduced | allowed

    def as_dict(self) -> dict:
        return asdict(self)


def risk_profile(risk_factor: int) -> RiskProfile:
    """Map an integer Risk Factor 1–10 to its enforced parameters."""
    rf = int(max(1, min(10, risk_factor)))
    # Step mappings (whole-unit params) mirror the prototype's brackets and hit
    # the spec anchors (1→1, 5→2, 10→4 new trades/day).
    if rf <= 3:
        per_day, earnings = 1, "never"
    elif rf <= 7:
        per_day, earnings = 2, "reduced"
    else:
        per_day, earnings = 4, "allowed"

    return RiskProfile(
        risk_factor=rf,
        max_position_pct=round(_piecewise(rf, 5, 12, 20), 2),
        max_exposure_pct=round(_piecewise(rf, 30, 70, 95), 2),
        min_conviction=round(_piecewise(rf, *CONVICTION_GATE_ANCHORS), 1),
        stop_atr_mult=round(_piecewise(rf, 1.5, 2.5, 3.5), 2),
        max_new_positions_per_day=per_day,
        trade_around_earnings=earnings,
    )
