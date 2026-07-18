"""Training labels.

The technical model predicts the probability of a *positive excess return over
the benchmark* over the next ``horizon`` trading days — a direction/probability
target, not a price target (spec §5A).
"""

from __future__ import annotations

import pandas as pd


def forward_excess_return(
    close: pd.Series, bench_close: pd.Series, horizon: int = 10
) -> pd.Series:
    """Forward ``horizon``-day return of ``close`` minus that of the benchmark.

    Uses ``shift(-horizon)`` so the value at row t looks at t+horizon; the last
    ``horizon`` rows are NaN (unknown future) and must be dropped for training.
    """
    bench = bench_close.reindex(close.index)
    fwd = close.shift(-horizon) / close - 1.0
    fwd_bench = bench.shift(-horizon) / bench - 1.0
    return fwd - fwd_bench


def binary_label(
    close: pd.Series, bench_close: pd.Series, horizon: int = 10
) -> pd.Series:
    """1 if forward excess return is positive, else 0. NaN where future unknown."""
    excess = forward_excess_return(close, bench_close, horizon)
    label = (excess > 0).astype("float")
    return label.where(excess.notna())
