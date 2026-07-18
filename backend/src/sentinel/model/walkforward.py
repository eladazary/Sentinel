"""Walk-forward training & out-of-sample prediction.

At each step we train on all data strictly *before* a cutoff date and predict the
next block of dates — never using future information. Run over the pooled panel
(all watchlist symbols), this yields a fully out-of-sample prediction series used
to evaluate the model and to drive the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sentinel.model.technical import (
    DEFAULT_PARAMS,
    TechnicalModel,
    prob_to_confidence,
    prob_to_score,
)


@dataclass
class WalkForwardResult:
    predictions: pd.DataFrame  # index=date, cols: symbol,label,prob,score,confidence
    n_folds: int
    n_predicted: int

    def auc(self) -> float | None:
        """Out-of-sample ROC AUC, or None if it can't be computed."""
        from sklearn.metrics import roc_auc_score

        y = self.predictions["label"]
        if y.nunique() < 2:
            return None
        return float(roc_auc_score(y, self.predictions["prob"]))

    def accuracy(self) -> float:
        pred = (self.predictions["prob"] >= 0.5).astype(int)
        return float((pred == self.predictions["label"]).mean())


def walk_forward_predict(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    label_col: str = "label",
    train_days: int = 756,
    step_days: int = 63,
    params: dict | None = None,
    num_boost_round: int = 300,
) -> WalkForwardResult:
    """Generate out-of-sample predictions over a date-indexed pooled panel.

    ``train_days`` / ``step_days`` are counts of unique trading dates: the first
    fold trains on the earliest ``train_days`` dates, predicts the next
    ``step_days``, then rolls forward.
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    df = df.sort_index()
    unique_dates = np.array(sorted(df.index.unique()))
    preds: list[pd.DataFrame] = []
    n_folds = 0

    i = train_days
    while i < len(unique_dates):
        cutoff = unique_dates[i]
        test_dates = set(unique_dates[i : i + step_days])

        train = df[df.index < cutoff]
        test = df[df.index.isin(test_dates)]
        i += step_days

        if test.empty or train.empty or train[label_col].nunique() < 2:
            continue

        model = TechnicalModel.train(
            train[feature_cols],
            train[label_col],
            params=params,
            num_boost_round=num_boost_round,
        )
        prob = model.predict_proba(test)
        block = pd.DataFrame(
            {
                "symbol": test["symbol"].values
                if "symbol" in test
                else "",
                "label": test[label_col].values,
                "prob": prob,
                "score": [prob_to_score(p) for p in prob],
                "confidence": [prob_to_confidence(p) for p in prob],
            },
            index=test.index,
        )
        preds.append(block)
        n_folds += 1

    if not preds:
        empty = pd.DataFrame(
            columns=["symbol", "label", "prob", "score", "confidence"]
        )
        return WalkForwardResult(empty, 0, 0)

    out = pd.concat(preds).sort_index()
    return WalkForwardResult(out, n_folds, len(out))
