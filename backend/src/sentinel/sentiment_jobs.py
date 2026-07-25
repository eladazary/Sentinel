"""Sentiment refresh job: run the news & social pipelines and persist results.

Produces the per-ticker aggregates the trading loop fuses (via ``sentiment_cache``),
stores the news feed, records directional calls by tracked accounts, scores
matured tracker calls, and refreshes the earnings calendar. Runs on a slower
cadence than the trading loop to respect source rate limits.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sentinel.config import Settings, Watchlist
from sentinel.logging_config import get_logger
from sentinel.models import NewsItemRow, SentimentCache
from sentinel.news.base import NewsSource
from sentinel.news.earnings import fetch_finnhub_earnings, upsert_earnings
from sentinel.news.pipeline import NewsPipeline
from sentinel.news.sources.edgar import EdgarSource
from sentinel.news.sources.finnhub import FinnhubSource
from sentinel.nlp.events import make_event_classifier
from sentinel.nlp.sentiment import make_sentiment_scorer
from sentinel.social.base import SocialSource
from sentinel.social.pipeline import SocialPipeline
from sentinel.social.sources.reddit import RedditSource
from sentinel.social.sources.stocktwits import StockTwitsSource
from sentinel.social import tracker

log = get_logger("sentinel.sentiment")

SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_news_sources(settings: Settings) -> list[NewsSource]:
    return [
        EdgarSource(
            settings.sec_user_agent,
            contact_ok=settings.has_valid_sec_user_agent,
        ),
        FinnhubSource(settings.finnhub_api_key),
    ]


def build_social_sources(settings: Settings) -> list[SocialSource]:
    return [StockTwitsSource(), RedditSource(settings)]


def _persist_news(session: Session, items) -> None:
    for it in items:
        stmt = insert(NewsItemRow).values(
            external_id=it.key(), symbol=it.symbol, ts=it.ts, source=it.source,
            headline=it.headline[:1000], url=it.url or None, event_type=it.event_type,
            materiality=it.materiality, sentiment_score=it.sentiment_score, impact=it.impact,
        ).on_conflict_do_nothing(index_elements=[NewsItemRow.external_id])
        session.execute(stmt)


def _upsert_cache(session: Session, symbol: str, news, social, now: datetime) -> None:
    """Persist sub-model scores, writing NULL where there was nothing to score.

    The aggregates return 0.0 for an empty input set, which the ensemble cannot
    distinguish from a genuine "balanced, therefore neutral" reading — it blends
    the 0.0 in at full weight and drags conviction toward zero. A source that is
    down or silent must be *absent* from the ensemble, not a confident neutral
    vote, so no-data is stored as NULL and skipped by the fusion step.

    A count of zero is the honest marker. Items that exist but score neutral are
    left as 0.0: that genuinely is information.
    """
    news_score = news.score if news.n_items else None
    news_conf = news.confidence if news.n_items else None
    social_score = social.score if social.n_posts else None
    social_conf = social.confidence if social.n_posts else None

    values = {
        "updated_at": now,
        "news_score": news_score,
        "news_confidence": news_conf,
        "news_drivers": news.drivers,
        "social_score": social_score,
        "social_confidence": social_conf,
        "social_crowding": social.crowding,
        "social_drivers": social.drivers,
    }
    stmt = insert(SentimentCache).values(symbol=symbol, **values).on_conflict_do_update(
        index_elements=[SentimentCache.symbol], set_=values
    )
    session.execute(stmt)


def refresh_sentiment(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    watchlist: Watchlist,
    sentiment=None,
    classifier=None,
) -> dict[str, dict]:
    """Refresh news+social aggregates for the whole watchlist. Returns a summary."""
    sentiment = sentiment or make_sentiment_scorer(settings)
    classifier = classifier or make_event_classifier(settings)
    news_pipe = NewsPipeline(build_news_sources(settings), sentiment, classifier, settings)

    now = datetime.now(timezone.utc)
    summary: dict[str, dict] = {}

    with session_factory() as session:
        cred = tracker.credibility_map(session)
        social_pipe = SocialPipeline(
            build_social_sources(settings), sentiment, settings,
            credibility_fn=lambda a, s: cred.get((a, s), 0.5),
        )
        tracked = set(cred.keys())

        for symbol in watchlist.symbols:
            news_items, news_agg = news_pipe.fetch_and_score(symbol, now=now)
            social_posts, social_agg = social_pipe.fetch_and_score(symbol, now=now)

            _persist_news(session, news_items)
            _upsert_cache(session, symbol, news_agg, social_agg, now)

            # Log directional calls by curated (tracked) accounts.
            for p in social_posts:
                if (p.author, p.source) in tracked and p.sentiment_score:
                    if abs(p.sentiment_score) >= 0.2:
                        tracker.record_call(
                            session, handle=p.author, source=p.source, symbol=symbol,
                            ts=p.ts, stance=1.0 if p.sentiment_score > 0 else -1.0,
                            text=p.text, price_at_call=None,
                        )
            summary[symbol] = {
                "news": news_agg.score, "news_n": news_agg.n_items,
                "social": social_agg.score, "social_n": social_agg.n_posts,
                "crowding": social_agg.crowding,
            }

        # Score matured tracker calls and refresh earnings.
        scored = tracker.score_pending_calls(session, now)
        if scored:
            log.info("scored %d matured tracker calls", scored)

        if settings.has_finnhub_key:
            for symbol in watchlist.symbols:
                for d, eps in fetch_finnhub_earnings(symbol, settings.finnhub_api_key):
                    upsert_earnings(session, symbol, d, eps)

    log.info("sentiment refresh complete for %d symbols", len(summary))
    return summary
