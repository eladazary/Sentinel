"""Optional Claude-API sentiment scorer (used when sentiment_engine=claude)."""

from __future__ import annotations

import json
import re

from sentinel.logging_config import get_logger
from sentinel.nlp.sentiment import LexiconScorer, SentimentResult

log = get_logger(__name__)

_PROMPT = """Score the finance sentiment of the text. Return ONLY JSON:
{{"score": <float -1..1>, "label": "positive|negative|neutral", "confidence": <float 0..1>}}
Text: {text}
JSON:"""


class ClaudeSentimentScorer:
    def __init__(self, settings):
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._fallback = LexiconScorer()

    def score(self, text: str) -> SentimentResult:
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=100,
                messages=[{"role": "user", "content": _PROMPT.format(text=text[:1500])}],
            )
            data = json.loads(re.search(r"\{.*\}", msg.content[0].text, re.S).group(0))
            return SentimentResult(
                score=float(max(-1.0, min(1.0, data["score"]))),
                label=str(data.get("label", "neutral")),
                confidence=float(max(0.0, min(1.0, data.get("confidence", 0.5)))),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude sentiment failed (%s); using lexicon", type(exc).__name__)
            return self._fallback.score(text)

    def score_batch(self, texts: list[str]) -> list[SentimentResult]:
        return [self.score(t) for t in texts]
