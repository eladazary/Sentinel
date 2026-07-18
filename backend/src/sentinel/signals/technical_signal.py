"""Produce the technical sub-model score + a plain-English driver list.

Every recommendation ships with its top drivers (spec §5 "no black-box trades").
Drivers here are derived from the feature row that fed the model, ranked by how
far each feature sits from neutral.
"""

from __future__ import annotations

import math

import pandas as pd

from sentinel.model.technical import TechnicalModel
from sentinel.signals.engine import SubScore


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def technical_drivers(f: pd.Series, top_n: int = 3) -> list[str]:
    """Rank the most salient technical features into readable phrases."""
    cands: list[tuple[float, str]] = []

    def add(salience: float, text: str) -> None:
        if not math.isnan(salience):
            cands.append((abs(salience), text))

    if "dist_ma50" in f and not pd.isna(f["dist_ma50"]):
        pos = "above" if f["dist_ma50"] >= 0 else "below"
        add(f["dist_ma50"], f"{_pct(f['dist_ma50'])} {pos} 50-day MA")
    if "ma50_over_ma200" in f and not pd.isna(f["ma50_over_ma200"]):
        state = "golden-cross uptrend" if f["ma50_over_ma200"] >= 0 else "death-cross downtrend"
        add(f["ma50_over_ma200"], f"50/200-day MA {state}")
    if "rsi14" in f and not pd.isna(f["rsi14"]):
        r = f["rsi14"]
        tag = " (overbought)" if r >= 70 else " (oversold)" if r <= 30 else ""
        add((r - 50) / 50.0, f"RSI {r:.0f}{tag}")
    if "macd_hist_norm" in f and not pd.isna(f["macd_hist_norm"]):
        sign = "positive" if f["macd_hist_norm"] >= 0 else "negative"
        add(f["macd_hist_norm"] * 100, f"MACD histogram {sign}")
    if "bb_pctb" in f and not pd.isna(f["bb_pctb"]):
        b = f["bb_pctb"]
        if b >= 0.9:
            add(b - 0.5, "Near upper Bollinger band")
        elif b <= 0.1:
            add(0.5 - b, "Near lower Bollinger band")
    if "vol_z" in f and not pd.isna(f["vol_z"]):
        add(f["vol_z"] / 3.0, f"Volume {f['vol_z']:+.1f}σ vs 30-day")
    if "rs_spy_20" in f and not pd.isna(f["rs_spy_20"]):
        add(f["rs_spy_20"], f"20d rel. strength vs SPY {_pct(f['rs_spy_20'])}")
    if "rs_sector_20" in f and not pd.isna(f["rs_sector_20"]):
        add(f["rs_sector_20"], f"20d rel. strength vs sector {_pct(f['rs_sector_20'])}")
    if "vix_pctile" in f and not pd.isna(f["vix_pctile"]):
        p = f["vix_pctile"]
        if p >= 0.8:
            add(p - 0.5, f"VIX in {p*100:.0f}th pct (stressed regime)")
        elif p <= 0.2:
            add(0.5 - p, f"VIX in {p*100:.0f}th pct (calm regime)")

    cands.sort(key=lambda c: c[0], reverse=True)
    return [text for _, text in cands[:top_n]]


def technical_subscore(
    model: TechnicalModel, features: pd.Series, weight: float
) -> SubScore:
    """Run the model on one feature row and package it as a SubScore."""
    _, score, confidence = model.predict_one(features)
    return SubScore(
        name="technical",
        score=score,
        confidence=confidence,
        weight=weight,
        drivers=technical_drivers(features),
    )
