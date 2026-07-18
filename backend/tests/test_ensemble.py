"""Full 3-model ensemble fusion (technical + news + social)."""

from __future__ import annotations

from sentinel.signals.engine import SubScore, fuse


def test_three_model_weighted_average():
    # Weights 0.45 / 0.30 / 0.10 → normalized over 0.85.
    subs = [
        SubScore("technical", -13.0, 0.5, 0.45),
        SubScore("news", -1.05, 0.4, 0.30),
        SubScore("social", 19.29, 0.2, 0.10),
    ]
    c = fuse(subs)
    expected = (-13.0 * 0.45 + -1.05 * 0.30 + 19.29 * 0.10) / 0.85
    assert abs(c.conviction - expected) < 1e-6
    assert set(c.per_model) == {"technical", "news", "social"}


def test_social_only_activates_when_present():
    # News/social absent → technical-only, conviction == its score.
    c = fuse([SubScore("technical", 40.0, 0.6, 0.45)])
    assert c.conviction == 40.0
    assert list(c.per_model) == ["technical"]


def test_disagreement_penalizes_confidence_three_models():
    subs = [
        SubScore("technical", 60.0, 1.0, 0.45),
        SubScore("news", -50.0, 1.0, 0.30),
        SubScore("social", -40.0, 1.0, 0.10),
    ]
    c = fuse(subs)
    assert c.confidence < 1.0  # tech disagrees with news+social
