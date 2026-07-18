"""Social scoring pipeline (spec §5C).

Per-post signal = FinBERT sentiment (or the source's pre-label) × author
credibility × recency decay × abnormal-engagement factor. Aggregated to a
per-ticker social momentum, with a **crowding flag**: extreme one-sided retail
sentiment is treated as a contrarian warning rather than confirmation.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sentinel.logging_config import get_logger
from sentinel.nlp.sentiment import SentimentScorer
from sentinel.social.base import SocialPost, SocialSource

log = get_logger(__name__)

CredibilityFn = Callable[[str, str], float]


def recency_decay(age_hours: float, half_life_hours: float) -> float:
    if half_life_hours <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age_hours) / half_life_hours)


def engagement_factor(engagement: float, mean: float, std: float) -> float:
    """1.0 at/below average; abnormally high engagement boosts up to ~2×."""
    if std <= 0:
        return 1.0
    z = (engagement - mean) / std
    return 1.0 + min(4.0, max(0.0, z)) * 0.25


@dataclass
class SocialAggregate:
    symbol: str
    score: float  # [-100, 100]
    confidence: float  # [0, 1]
    n_posts: int
    crowding: bool
    bull_fraction: float
    drivers: list[str] = field(default_factory=list)


def aggregate(
    posts: list[SocialPost],
    *,
    now: datetime,
    half_life_hours: float,
    crowding_threshold: float,
    min_posts_for_crowding: int = 5,
) -> SocialAggregate:
    if not posts:
        return SocialAggregate("", 0.0, 0.0, 0, False, 0.5, [])
    symbol = posts[0].symbol
    engagements = [p.engagement for p in posts]
    mean_e = statistics.fmean(engagements)
    std_e = statistics.pstdev(engagements) if len(engagements) > 1 else 0.0

    num = den = 0.0
    cred_sum = 0.0
    bull = bear = 0
    weighted: list[tuple[float, SocialPost, str]] = []
    for p in posts:
        if p.sentiment_score is None:
            continue
        s = p.sentiment_score
        if s > 0.05:
            bull += 1
        elif s < -0.05:
            bear += 1
        age_h = (now - p.ts).total_seconds() / 3600.0
        cred = p.credibility if p.credibility is not None else 0.5
        w = cred * recency_decay(age_h, half_life_hours) * engagement_factor(
            p.engagement, mean_e, std_e
        )
        num += s * w
        den += w
        cred_sum += cred
        weighted.append((abs(s) * w, p, f"@{p.author} ({p.source})"))

    directional = bull + bear
    bull_fraction = (bull / directional) if directional else 0.5
    one_sided = max(bull_fraction, 1.0 - bull_fraction)
    crowding = directional >= min_posts_for_crowding and one_sided >= crowding_threshold

    raw = (num / den) if den else 0.0  # [-1, 1]
    if crowding:
        # Contrarian: extreme one-sided sentiment fades rather than confirms.
        majority_sign = 1.0 if bull_fraction >= 0.5 else -1.0
        score = round(-majority_sign * one_sided * 40.0, 2)
    else:
        score = round(raw * 100.0, 2)

    confidence = round(min(1.0, len(posts) / 10.0) * (cred_sum / max(1, len(posts))), 4)
    weighted.sort(key=lambda x: x[0], reverse=True)
    drivers = [label for _, _, label in weighted[:3]]
    if crowding:
        drivers.insert(0, f"crowding: {one_sided*100:.0f}% one-sided → contrarian")

    return SocialAggregate(
        symbol=symbol,
        score=score,
        confidence=confidence,
        n_posts=len(posts),
        crowding=crowding,
        bull_fraction=round(bull_fraction, 3),
        drivers=drivers[:3],
    )


class SocialPipeline:
    def __init__(
        self,
        sources: list[SocialSource],
        sentiment: SentimentScorer,
        settings,
        credibility_fn: CredibilityFn | None = None,
    ):
        self.sources = [s for s in sources if s.available()]
        self.sentiment = sentiment
        self.settings = settings
        self.credibility_fn = credibility_fn or (lambda author, source: 0.5)

    def fetch_and_score(
        self, symbol: str, *, now: datetime | None = None
    ) -> tuple[list[SocialPost], SocialAggregate]:
        now = now or datetime.now(timezone.utc)
        raw: dict[str, SocialPost] = {}
        for src in self.sources:
            for p in src.fetch(symbol):
                raw.setdefault(p.key(), p)
        posts = list(raw.values())

        # Sentiment: use the source's pre-label when present, else FinBERT.
        to_score = [p for p in posts if p.stance is None]
        scored = self.sentiment.score_batch([p.text for p in to_score]) if to_score else []
        it = iter(scored)
        for p in posts:
            p.sentiment_score = p.stance if p.stance is not None else next(it).score
            p.credibility = self.credibility_fn(p.author, p.source)

        agg = aggregate(
            posts,
            now=now,
            half_life_hours=self.settings.social_half_life_hours,
            crowding_threshold=self.settings.crowding_threshold,
        )
        return posts, agg
