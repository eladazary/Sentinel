"""News pipeline scoring tests (pure functions + orchestration with fakes)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.news.base import NewsItem
from sentinel.news.pipeline import (
    NewsPipeline,
    aggregate,
    decay_weight,
    item_impact,
)
from sentinel.nlp.events import RuleEventClassifier
from sentinel.nlp.sentiment import LexiconScorer


def test_decay_weight_halves_at_half_life():
    assert decay_weight(0, 2) == 1.0
    assert decay_weight(2, 2) == 0.5
    assert round(decay_weight(4, 2), 3) == 0.25


def test_item_impact_scales_with_materiality():
    strong = item_impact(1.0, 1.0, materiality=5, novelty=1.0)
    weak = item_impact(1.0, 1.0, materiality=1, novelty=1.0)
    assert strong > weak
    assert -1.0 <= strong <= 1.0


def test_item_impact_novelty_zero_kills_signal():
    assert item_impact(1.0, 1.0, materiality=5, novelty=0.0) == 0.0


def _scored_item(symbol, ts, impact, event_type=None):
    it = NewsItem(symbol=symbol, ts=ts, source="t", headline="h")
    it.impact = impact
    it.materiality = 4
    it.event_type = event_type
    it.sentiment_score = impact
    return it


def test_aggregate_recent_dominates():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    items = [
        _scored_item("NVDA", now, 0.8),  # fresh positive
        _scored_item("NVDA", now - timedelta(days=10), -0.8),  # stale negative
    ]
    agg = aggregate(items, now=now, news_half_life=2.0, earnings_half_life=5.0)
    assert agg.score > 0  # recent positive outweighs decayed negative
    assert -100 <= agg.score <= 100
    assert agg.n_items == 2


def test_aggregate_empty():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    assert aggregate([], now=now, news_half_life=2, earnings_half_life=5).score == 0.0


class FakeSource:
    name = "fake"

    def __init__(self, items):
        self._items = items

    def available(self):
        return True

    def fetch(self, symbol, since, limit=50):
        return list(self._items)


class _Settings:
    news_half_life_days = 2.0
    earnings_news_half_life_days = 5.0


def test_pipeline_scores_items():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    items = [
        NewsItem("NVDA", now, "fake", "NVDA beats earnings, stock surges", "record quarter",
                 external_id="a"),
        NewsItem("NVDA", now, "fake", "NVDA hit with lawsuit and downgrade", "probe",
                 external_id="b"),
    ]
    pipe = NewsPipeline([FakeSource(items)], LexiconScorer(), RuleEventClassifier(), _Settings())
    scored, agg = pipe.fetch_and_score("NVDA", now=now)
    assert len(scored) == 2
    assert all(s.impact is not None for s in scored)
    assert agg.n_items == 2
    assert len(agg.drivers) > 0


# ---- EDGAR contact requirement (SEC 403s without one) ----

def test_sec_user_agent_validation():
    from sentinel.config import Settings

    # The shipped default is deliberately invalid.
    assert Settings().has_valid_sec_user_agent is False
    assert Settings(
        sec_user_agent="Sentinel/0.1 (elad@example.com)"
    ).has_valid_sec_user_agent is True
    # No contact address at all — SEC rejects these.
    assert Settings(sec_user_agent="Sentinel/0.1").has_valid_sec_user_agent is False
    assert Settings(sec_user_agent="").has_valid_sec_user_agent is False


def test_edgar_skips_entirely_without_a_contact(monkeypatch):
    """Must not spend a request to learn what config already tells us."""
    import httpx

    from sentinel.news.sources.edgar import EdgarSource

    def boom(*a, **kw):
        raise AssertionError("EDGAR attempted a request with no contact address")

    monkeypatch.setattr(httpx.Client, "get", boom)
    src = EdgarSource("Sentinel/0.1 (contact: set SENTINEL_SEC_USER_AGENT)", contact_ok=False)
    assert src.available() is False
    assert src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc)) == []


def test_edgar_is_available_with_a_contact():
    from sentinel.news.sources.edgar import EdgarSource

    assert EdgarSource("Sentinel/0.1 (a@b.com)", contact_ok=True).available() is True
