"""Social pipeline scoring + crowding tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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


# ---- StockTwits Cloudflare challenge handling ----

def _resp(status, headers=None):
    import httpx

    return httpx.Response(
        status_code=status,
        headers=headers or {},
        request=httpx.Request("GET", "https://api.stocktwits.com/x"),
    )


def test_bot_challenge_detection():
    from sentinel.social.sources.stocktwits import _is_bot_challenge

    assert _is_bot_challenge(_resp(403, {"cf-mitigated": "challenge"})) is True
    assert _is_bot_challenge(_resp(403, {"content-type": "text/html"})) is True
    assert _is_bot_challenge(_resp(503, {"content-type": "text/html; charset=UTF-8"})) is True
    # A genuine API rejection is not a challenge — it should stay a warning.
    assert _is_bot_challenge(_resp(404, {"content-type": "application/json"})) is False
    assert _is_bot_challenge(_resp(200, {"content-type": "application/json"})) is False


def test_challenge_parks_the_source_instead_of_retrying(monkeypatch):
    import httpx

    from sentinel.social.sources.stocktwits import StockTwitsSource

    calls = []

    def challenged(self, *a, **kw):
        calls.append(1)
        return _resp(403, {"cf-mitigated": "challenge", "content-type": "text/html"})

    monkeypatch.setattr(httpx.Client, "get", challenged)
    src = StockTwitsSource()
    assert src.available() is True
    assert src.fetch("MSFT") == []
    assert src.available() is False  # parked

    # Subsequent refreshes must not touch the network while parked.
    assert src.fetch("AAPL") == []
    assert len(calls) == 1


# ---- no-data must not become a confident neutral vote ----

def test_empty_aggregate_is_persisted_as_null_not_zero():
    """A blocked source must be absent from the ensemble, not vote 0.0.

    aggregate([]) returns score 0.0, and the fusion step only skips a sub-model
    whose score is None — so persisting 0.0 blended a dead source in at full
    weight and dragged conviction toward zero.
    """
    from sentinel.news.pipeline import NewsAggregate
    from sentinel.sentiment_jobs import _upsert_cache
    from sentinel.social.pipeline import SocialAggregate

    captured = {}

    class FakeSession:
        def execute(self, stmt):
            captured.update(stmt.compile().params)

    empty_news = NewsAggregate("AAPL", 0.0, 0.0, 0, [])
    empty_social = SocialAggregate("AAPL", 0.0, 0.0, 0, False, 0.5, [])
    _upsert_cache(FakeSession(), "AAPL", empty_news, empty_social, datetime.now(timezone.utc))

    assert captured["news_score"] is None
    assert captured["news_confidence"] is None
    assert captured["social_score"] is None
    assert captured["social_confidence"] is None


def test_real_data_is_persisted_including_a_genuine_neutral():
    """Posts that exist but balance out ARE information — keep the 0.0."""
    from sentinel.news.pipeline import NewsAggregate
    from sentinel.sentiment_jobs import _upsert_cache
    from sentinel.social.pipeline import SocialAggregate

    captured = {}

    class FakeSession:
        def execute(self, stmt):
            captured.update(stmt.compile().params)

    news = NewsAggregate("AAPL", -1.1, 0.05, 1, ["8-K"])
    social = SocialAggregate("AAPL", 0.0, 0.8, 12, False, 0.5, ["@a"])
    _upsert_cache(FakeSession(), "AAPL", news, social, datetime.now(timezone.utc))

    assert captured["news_score"] == -1.1
    assert captured["social_score"] == 0.0  # balanced, not absent
    assert captured["social_confidence"] == 0.8


def test_fusion_excludes_a_missing_sub_model_entirely():
    """The payoff: a dead source must not dilute the technical read."""
    from sentinel.signals.engine import SubScore, fuse

    tech_only = fuse([SubScore("technical", 36.38, 0.5, 0.45)])
    with_dead_social = fuse([
        SubScore("technical", 36.38, 0.5, 0.45),
        SubScore("social", 0.0, 0.0, 0.10),  # what the bug produced
    ])
    assert tech_only.conviction == pytest.approx(36.38, abs=0.01)
    assert with_dead_social.conviction < tech_only.conviction  # the dilution
