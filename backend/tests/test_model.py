"""Technical model + walk-forward tests (tiny synthetic data, fast)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.features.engineering import FEATURE_COLUMNS
from sentinel.model.technical import (
    TechnicalModel,
    prob_to_confidence,
    prob_to_score,
)
from sentinel.model.walkforward import walk_forward_predict


def _learnable_dataset(n=1200, seed=0):
    """Feature 0 is predictive of the label; the rest is noise."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    prob = 1 / (1 + np.exp(-2.5 * X["dist_ma20"]))
    y = pd.Series((rng.uniform(size=n) < prob).astype(int), name="label")
    return X, y


def test_prob_mappings():
    assert prob_to_score(0.5) == 0.0
    assert prob_to_score(1.0) == 100.0
    assert prob_to_score(0.0) == -100.0
    assert prob_to_confidence(0.5) == 0.0
    assert prob_to_confidence(1.0) == 1.0


def test_train_predict_and_learns_signal():
    X, y = _learnable_dataset()
    model = TechnicalModel.train(X, y, num_boost_round=120)
    proba = model.predict_proba(X)
    assert proba.shape == (len(X),)
    assert ((proba >= 0) & (proba <= 1)).all()
    # dist_ma20 should be the most important feature.
    imp = model.feature_importance()
    assert max(imp, key=imp.get) == "dist_ma20"


def test_predict_one_shapes():
    X, y = _learnable_dataset(n=400)
    model = TechnicalModel.train(X, y, num_boost_round=60)
    p, score, conf = model.predict_one(X.iloc[10])
    assert 0 <= p <= 1
    assert -100 <= score <= 100
    assert 0 <= conf <= 1


def test_save_and_load(tmp_path):
    X, y = _learnable_dataset(n=400)
    model = TechnicalModel.train(X, y, num_boost_round=40, trained_through="2024-01-01")
    model.save(tmp_path, name="t")
    loaded = TechnicalModel.load(tmp_path, name="t")
    assert loaded.feature_columns == FEATURE_COLUMNS
    assert loaded.trained_through == "2024-01-01"
    np.testing.assert_allclose(model.predict_proba(X), loaded.predict_proba(X))


def test_walk_forward_produces_oos_predictions():
    X, y = _learnable_dataset(n=1500)
    idx = pd.date_range("2018-01-01", periods=len(X), freq="B")
    df = X.copy()
    df["label"] = y.values
    df["symbol"] = "SYN"
    df.index = idx
    res = walk_forward_predict(
        df, FEATURE_COLUMNS, train_days=500, step_days=250, num_boost_round=60
    )
    assert res.n_folds >= 2
    assert res.n_predicted > 0
    auc = res.auc()
    assert auc is not None and auc > 0.6  # signal is learnable
