"""Ensemble fusion and signal derivation.

The spec (§5) fuses three sub-models (technical/news/social) into a conviction in
[-100, +100]. Phase 1 ships only the technical model; news/social arrive in
Phase 2, so the ensemble normalises weights across whichever sub-models are
*active*.

Design note on the formula: the spec writes conviction = Σ(wᵢ·scoreᵢ·confᵢ). With
a single, deliberately weak model that collapses conviction toward zero (no
trades ever fire). We instead use a **weight-normalised average of scores** for
conviction and fold confidence in separately — including the spec's "disagreement
lowers effective confidence" rule — so confidence gates/sizes trades at the risk
layer rather than silently zeroing the score. Behaviour converges as more
sub-models come online.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Default ensemble weights (spec §5). Only active sub-models contribute; weights
# are renormalised over those present.
DEFAULT_WEIGHTS = {"technical": 0.45, "news": 0.30, "social": 0.25}


@dataclass
class SubScore:
    """One sub-model's output for one ticker."""

    name: str
    score: float  # [-100, 100]
    confidence: float  # [0, 1]
    weight: float
    drivers: list[str] = field(default_factory=list)


@dataclass
class Conviction:
    conviction: float  # [-100, 100]
    confidence: float  # [0, 1]
    per_model: dict[str, float]  # name -> score
    drivers: list[str]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def fuse(subscores: list[SubScore]) -> Conviction:
    """Fuse active sub-model scores into a conviction + confidence."""
    active = [s for s in subscores if s.weight > 0]
    if not active:
        return Conviction(0.0, 0.0, {}, [])

    wsum = sum(s.weight for s in active)
    norm = {s.name: s.weight / wsum for s in active}

    conviction = sum(norm[s.name] * s.score for s in active)
    base_conf = sum(norm[s.name] * s.confidence for s in active)

    # Disagreement penalty: if models point opposite ways, lower confidence.
    penalty = 1.0
    if len(active) > 1:
        signs = {1 if s.score > 0 else (-1 if s.score < 0 else 0) for s in active}
        if 1 in signs and -1 in signs:
            # Spread of scores in [0, 200] -> penalty in [0.5, 1.0].
            spread = max(s.score for s in active) - min(s.score for s in active)
            penalty = _clamp(1.0 - (spread / 200.0) * 0.5, 0.5, 1.0)

    confidence = _clamp(base_conf * penalty, 0.0, 1.0)

    # Top-3 drivers, ordered by the active models' weighted absolute contribution.
    ordered = sorted(active, key=lambda s: abs(norm[s.name] * s.score), reverse=True)
    drivers: list[str] = []
    for s in ordered:
        drivers.extend(s.drivers)
    drivers = drivers[:3]

    return Conviction(
        conviction=_clamp(conviction, -100.0, 100.0),
        confidence=confidence,
        per_model={s.name: s.score for s in active},
        drivers=drivers,
    )


def derive_signal(
    conviction: float,
    gate: float,
    has_position: bool,
    *,
    trim_below: float = 0.0,
    sell_below: float = -40.0,
) -> str:
    """Map conviction → action chip.

    No position: BUY at/above the conviction gate, else PASS.
    Holding:     SELL if strongly negative, TRIM if weak/negative, else HOLD.
    """
    if not has_position:
        return "BUY" if conviction >= gate else "PASS"
    if conviction <= sell_below:
        return "SELL"
    if conviction < trim_below:
        return "TRIM"
    return "HOLD"
