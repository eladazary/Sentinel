"""Social pipeline scoring + crowding tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.social.base import SocialPost
from sentinel.social.pipeline import (
    SocialPipeline,
    aggregate,
    engagement_factor,
    recency_decay,
)
from sentinel.social.tracker import credibility_from_stats


def test_recency_decay():
    assert recency_decay(0, 24) == 1.0
    assert recency_decay(24, 24) == 0.5


def test_engagement_factor_boosts_abnormal():
    assert engagement_factor(100, mean=100, std=0) == 1.0  # no variance
    assert engagement_factor(200, mean=100, std=50) > 1.0  # +2σ boosts
    assert engagement_factor(50, mean=100, std=50) == 1.0  # below avg -> no boost


def _post(symbol, ts, sentiment, eng=10.0, author="a", cred=0.5):
    p = SocialPost(symbol=symbol, ts=ts, source="stocktwits", author=author, text="x", engagement=eng)
    p.sentiment_score = sentiment
    p.credibility = cred
    return p


def test_aggregate_bullish_momentum():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    posts = [_post("NVDA", now, 0.6), _post("NVDA", now, 0.4)]
    agg = aggregate(posts, now=now, half_life_hours=24, crowding_threshold=0.9)
    assert agg.score > 0
    assert not agg.crowding


def test_aggregate_crowding_is_contrarian():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    # 8 uniformly bullish posts -> one-sided -> crowding -> contrarian (negative) score.
    posts = [_post("NVDA", now, 0.8, author=f"u{i}") for i in range(8)]
    agg = aggregate(posts, now=now, half_life_hours=24, crowding_threshold=0.9)
    assert agg.crowding
    assert agg.score < 0  # extreme bullishness flips to a warning
    assert agg.bull_fraction == 1.0
    assert any("crowding" in d for d in agg.drivers)


def test_credibility_prior_is_neutral():
    assert credibility_from_stats(0, 0) == 0.5  # no evidence -> neutral
    assert credibility_from_stats(20, 18) > 0.7  # strong record -> high
    assert credibility_from_stats(20, 2) < 0.3  # poor record -> low


class FakeSource:
    name = "stocktwits"

    def __init__(self, posts):
        self._posts = posts

    def available(self):
        return True

    def fetch(self, symbol, limit=50):
        return list(self._posts)


class _Settings:
    social_half_life_hours = 24.0
    crowding_threshold = 0.9


def test_pipeline_uses_stance_and_credibility():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    posts = [
        SocialPost("NVDA", now, "stocktwits", "bull_guy", "to the moon", 50, stance=1.0, external_id="1"),
        SocialPost("NVDA", now - timedelta(hours=1), "stocktwits", "bear_guy", "overvalued", 5, stance=-1.0, external_id="2"),
    ]
    cred = {("bull_guy", "stocktwits"): 0.9}
    pipe = SocialPipeline(
        [FakeSource(posts)], sentiment=None, settings=_Settings(),
        credibility_fn=lambda a, s: cred.get((a, s), 0.5),
    )
    scored, agg = pipe.fetch_and_score("NVDA", now=now)
    assert scored[0].sentiment_score == 1.0  # stance used, no FinBERT needed
    assert scored[0].credibility == 0.9
    assert agg.n_posts == 2
