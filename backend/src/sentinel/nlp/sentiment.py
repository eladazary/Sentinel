"""Finance sentiment scoring.

Behind a small ``SentimentScorer`` protocol so the heavy FinBERT model, a Claude
API scorer, and a dependency-free lexicon fallback are interchangeable. Scores
are normalized to [-1, 1] with a [0, 1] confidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sentinel.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class SentimentResult:
    score: float  # [-1, 1]  (positive - negative)
    label: str  # positive | negative | neutral
    confidence: float  # [0, 1]


class SentimentScorer(Protocol):
    def score(self, text: str) -> SentimentResult: ...

    def score_batch(self, texts: list[str]) -> list[SentimentResult]: ...


# --- lightweight lexicon fallback (no deps, used in tests / when models absent) ---

_POS = {
    "beat", "beats", "surge", "surges", "rally", "record", "upgrade", "upgraded",
    "growth", "strong", "gain", "gains", "jumps", "soars", "bullish", "outperform",
    "raise", "raised", "tops", "positive", "profit", "wins", "approval", "expands",
}
_NEG = {
    "miss", "misses", "plunge", "plunges", "cut", "cuts", "downgrade", "downgraded",
    "weak", "loss", "losses", "falls", "drop", "drops", "bearish", "underperform",
    "lawsuit", "probe", "recall", "warning", "negative", "slump", "layoffs", "fraud",
}


class LexiconScorer:
    """Naive bag-of-words sentiment. Weak, but zero-dependency and deterministic."""

    def score(self, text: str) -> SentimentResult:
        toks = [t.strip(".,!?:;()[]\"'").lower() for t in (text or "").split()]
        pos = sum(t in _POS for t in toks)
        neg = sum(t in _NEG for t in toks)
        total = pos + neg
        if total == 0:
            return SentimentResult(0.0, "neutral", 0.0)
        score = (pos - neg) / total
        label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        confidence = min(1.0, total / 5.0)
        return SentimentResult(score, label, confidence)

    def score_batch(self, texts: list[str]) -> list[SentimentResult]:
        return [self.score(t) for t in texts]


# --- FinBERT (lazy-loaded; heavy) ---

class FinBertScorer:
    """ProsusAI/finbert via transformers. Model loads lazily on first use."""

    def __init__(self, model_name: str = "ProsusAI/finbert"):
        self._model_name = model_name
        self._pipe = None

    def _pipeline(self):
        if self._pipe is None:
            from transformers import pipeline  # heavy import, deferred

            log.info("loading FinBERT (%s)...", self._model_name)
            self._pipe = pipeline(
                "text-classification",
                model=self._model_name,
                top_k=None,
                truncation=True,
                max_length=512,
            )
        return self._pipe

    @staticmethod
    def _to_result(scores: list[dict]) -> SentimentResult:
        by = {s["label"].lower(): s["score"] for s in scores}
        pos, neg = by.get("positive", 0.0), by.get("negative", 0.0)
        neu = by.get("neutral", 0.0)
        score = pos - neg
        label = max(("positive", pos), ("negative", neg), ("neutral", neu), key=lambda x: x[1])[0]
        return SentimentResult(score=score, label=label, confidence=max(pos, neg, neu))

    def score(self, text: str) -> SentimentResult:
        return self.score_batch([text])[0]

    def score_batch(self, texts: list[str]) -> list[SentimentResult]:
        if not texts:
            return []
        clean = [t if (t and t.strip()) else "." for t in texts]
        raw = self._pipeline()(clean)
        # top_k=None yields a list-of-lists (one list of label dicts per input).
        return [self._to_result(item) for item in raw]


def make_sentiment_scorer(settings) -> SentimentScorer:
    """Pick a scorer from settings, degrading gracefully if a backend is missing."""
    engine = settings.sentiment_engine
    if engine == "lexicon":
        return LexiconScorer()
    if engine == "claude":
        from sentinel.nlp.claude_sentiment import ClaudeSentimentScorer

        if settings.has_anthropic_key:
            return ClaudeSentimentScorer(settings)
        log.warning("sentiment_engine=claude but no ANTHROPIC key; using lexicon")
        return LexiconScorer()
    # finbert (default)
    try:
        import transformers  # noqa: F401  (probe availability)

        return FinBertScorer()
    except Exception:  # noqa: BLE001
        log.warning("transformers unavailable; falling back to lexicon scorer")
        return LexiconScorer()
