"""News scoring pipeline: fetch → sentiment + event classify → decayed aggregate.

Per-item impact fuses FinBERT sentiment with the event classifier's direction,
weighted by materiality and novelty. Items decay with a half-life (longer for
earnings), then aggregate to a per-ticker news score in [-100, 100] (spec §5B).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sentinel.logging_config import get_logger
from sentinel.news.base import NewsItem, NewsSource
from sentinel.nlp.events import EventClassifier
from sentinel.nlp.sentiment import SentimentScorer

log = get_logger(__name__)

_EARNINGS_EVENTS = {"earnings", "guidance"}


def decay_weight(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age_days) / half_life_days)


def item_impact(
    sentiment_score: float, event_direction: float, materiality: int, novelty: float
) -> float:
    """Signed per-item impact in [-1, 1] before time decay."""
    base = 0.6 * sentiment_score + 0.4 * event_direction
    scaled = base * (materiality / 5.0) * max(0.0, min(1.0, novelty))
    return max(-1.0, min(1.0, scaled))


@dataclass
class NewsAggregate:
    symbol: str
    score: float  # [-100, 100]
    confidence: float  # [0, 1]
    n_items: int
    drivers: list[str] = field(default_factory=list)


def aggregate(
    items: list[NewsItem],
    *,
    now: datetime,
    news_half_life: float,
    earnings_half_life: float,
) -> NewsAggregate:
    """Aggregate scored items (each must have impact/materiality/event set)."""
    if not items:
        return NewsAggregate("", 0.0, 0.0, 0, [])
    symbol = items[0].symbol
    num = den = 0.0
    conf_num = 0.0
    weighted: list[tuple[float, NewsItem]] = []
    for it in items:
        if it.impact is None:
            continue
        age_days = (now - it.ts).total_seconds() / 86400.0
        hl = earnings_half_life if (it.event_type in _EARNINGS_EVENTS) else news_half_life
        w = decay_weight(age_days, hl)
        num += it.impact * w
        den += w
        conf_num += (it.sentiment_score is not None) * w
        weighted.append((abs(it.impact) * w, it))
    if den == 0:
        return NewsAggregate(symbol, 0.0, 0.0, len(items), [])

    net = num / den  # [-1, 1]
    volume_factor = min(1.0, den / 3.0)
    confidence = round(min(1.0, volume_factor), 4)

    weighted.sort(key=lambda x: x[0], reverse=True)
    drivers = [
        f"{it.source}: {it.headline}"
        + (f" ({it.event_type}, mat {it.materiality})" if it.event_type else "")
        for _, it in weighted[:3]
    ]
    return NewsAggregate(
        symbol=symbol,
        score=round(net * 100.0, 2),
        confidence=confidence,
        n_items=len(items),
        drivers=drivers,
    )


class NewsPipeline:
    def __init__(
        self,
        sources: list[NewsSource],
        sentiment: SentimentScorer,
        classifier: EventClassifier,
        settings,
    ):
        self.sources = [s for s in sources if s.available()]
        self.sentiment = sentiment
        self.classifier = classifier
        self.settings = settings

    def fetch_and_score(
        self, symbol: str, *, lookback_days: int = 7, now: datetime | None = None
    ) -> tuple[list[NewsItem], NewsAggregate]:
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=lookback_days)

        raw: dict[str, NewsItem] = {}
        for src in self.sources:
            for it in src.fetch(symbol, since):
                raw.setdefault(it.key(), it)
        items = list(raw.values())

        # Score each item.
        texts = [f"{it.headline}. {it.summary}".strip() for it in items]
        sentiments = self.sentiment.score_batch(texts) if texts else []
        for it, sent in zip(items, sentiments):
            ev = self.classifier.classify(it.headline, it.summary)
            # A source may hint an event type (e.g. a 10-Q → earnings); keep it if set.
            it.event_type = it.event_type or ev.event_type
            it.materiality = ev.materiality
            it.direction = ev.direction
            it.sentiment_score = sent.score
            it.sentiment_label = sent.label
            it.impact = item_impact(sent.score, ev.direction, ev.materiality, ev.novelty)

        agg = aggregate(
            items,
            now=now,
            news_half_life=self.settings.news_half_life_days,
            earnings_half_life=self.settings.earnings_news_half_life_days,
        )
        return items, agg
