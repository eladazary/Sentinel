"""LightGBM technical model.

Binary classifier predicting P(positive excess return over the benchmark within
``label_horizon_days``). The probability is mapped to:

* score      = (p - 0.5) * 200  → [-100, +100]  (the technical sub-model score)
* confidence = 2 * |p - 0.5|     → [0, 1]        (0 at a coin-flip, 1 at extremes)

LSTM/transformer price prediction is explicitly rejected for v1 (spec §5A).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from sentinel.features.engineering import FEATURE_COLUMNS

# Conservative defaults for a small, noisy financial dataset: shallow trees,
# strong regularisation, subsampling. Tuned for stability over peak fit.
DEFAULT_PARAMS: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 15,
    "max_depth": 4,
    "learning_rate": 0.03,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "min_child_samples": 40,
    "lambda_l1": 0.0,
    "lambda_l2": 1.0,
    "verbosity": -1,
}


def prob_to_score(p: float) -> float:
    return float((p - 0.5) * 200.0)


def prob_to_confidence(p: float) -> float:
    return float(min(1.0, max(0.0, 2.0 * abs(p - 0.5))))


@dataclass
class TechnicalModel:
    """Trained LightGBM classifier plus its feature contract and metadata."""

    booster: lgb.Booster
    feature_columns: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    n_estimators: int = 0
    train_rows: int = 0
    trained_through: str | None = None  # ISO date of last training example

    # ---- training ----
    @classmethod
    def train(
        cls,
        X: pd.DataFrame,
        y: pd.Series,
        *,
        params: dict | None = None,
        num_boost_round: int = 400,
        trained_through: str | None = None,
    ) -> "TechnicalModel":
        params = {**DEFAULT_PARAMS, **(params or {})}
        cols = list(X.columns)
        dtrain = lgb.Dataset(X.values, label=y.values, feature_name=cols)
        booster = lgb.train(params, dtrain, num_boost_round=num_boost_round)
        return cls(
            booster=booster,
            feature_columns=cols,
            n_estimators=booster.num_trees(),
            train_rows=len(X),
            trained_through=trained_through,
        )

    # ---- inference ----
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """P(positive excess return) for each row, ordered like X."""
        return self.booster.predict(X[self.feature_columns].values)

    def predict_one(self, features: pd.Series) -> tuple[float, float, float]:
        """Return (probability, score, confidence) for a single feature row."""
        X = features[self.feature_columns].to_frame().T
        p = float(self.predict_proba(X)[0])
        return p, prob_to_score(p), prob_to_confidence(p)

    def feature_importance(self) -> dict[str, float]:
        gains = self.booster.feature_importance(importance_type="gain")
        return dict(zip(self.feature_columns, (float(g) for g in gains)))

    # ---- persistence ----
    def save(self, directory: str | Path, name: str = "technical") -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        model_path = d / f"{name}.txt"
        meta_path = d / f"{name}.meta.json"
        self.booster.save_model(str(model_path))
        meta = {
            "feature_columns": self.feature_columns,
            "n_estimators": self.n_estimators,
            "train_rows": self.train_rows,
            "trained_through": self.trained_through,
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        return model_path

    @classmethod
    def load(cls, directory: str | Path, name: str = "technical") -> "TechnicalModel":
        d = Path(directory)
        booster = lgb.Booster(model_file=str(d / f"{name}.txt"))
        meta = json.loads((d / f"{name}.meta.json").read_text())
        return cls(
            booster=booster,
            feature_columns=meta["feature_columns"],
            n_estimators=meta.get("n_estimators", booster.num_trees()),
            train_rows=meta.get("train_rows", 0),
            trained_through=meta.get("trained_through"),
        )
