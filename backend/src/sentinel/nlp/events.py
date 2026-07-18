"""News event classification (spec §5B).

Classifies a headline/body into event type, materiality (1–5), direction, and
novelty. Uses the Claude API when a key is configured; otherwise a transparent
rule-based classifier so the pipeline runs offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from sentinel.logging_config import get_logger

log = get_logger(__name__)

EVENT_TYPES = (
    "earnings", "guidance", "litigation", "product", "macro", "m&a", "analyst", "other"
)


@dataclass
class EventResult:
    event_type: str
    materiality: int  # 1–5
    direction: float  # [-1, 1]
    novelty: float  # [0, 1]


# --- rule-based (keyword) classifier ---

_PATTERNS: list[tuple[str, str, int]] = [
    # (event_type, regex, base materiality)
    ("earnings", r"\b(earnings|eps|revenue|quarter(ly)?|q[1-4]\b|beat|miss(es|ed)?)\b", 4),
    ("guidance", r"\b(guidance|forecast|outlook|raises?|cuts? (its )?forecast)\b", 4),
    ("m&a", r"\b(acqui(re|res|sition)|merg(er|es)|buyout|takeover|to buy)\b", 5),
    ("litigation", r"\b(lawsuit|sued?|settlement|probe|investigation|antitrust|fine[sd]?)\b", 4),
    ("analyst", r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiat(e|es|ed) coverage|rating)\b", 3),
    ("product", r"\b(launch(es|ed)?|unveil(s|ed)?|recall|approval|fda|chip|model|release[sd]?)\b", 3),
    ("macro", r"\b(fed|inflation|rate (hike|cut)|tariff|gdp|jobs report|cpi)\b", 2),
]
_POS_DIR = re.compile(r"\b(beat|beats|raises?|upgrade[sd]?|approval|surge|record|wins?|tops)\b", re.I)
_NEG_DIR = re.compile(r"\b(miss(es|ed)?|cuts?|downgrade[sd]?|recall|lawsuit|probe|fine[sd]?|plunge|warning)\b", re.I)


class RuleEventClassifier:
    def classify(self, headline: str, body: str | None = None) -> EventResult:
        text = f"{headline} {body or ''}".strip()
        event_type, materiality = "other", 2
        for etype, pat, mat in _PATTERNS:
            if re.search(pat, text, re.I):
                event_type, materiality = etype, mat
                break
        pos, neg = len(_POS_DIR.findall(text)), len(_NEG_DIR.findall(text))
        direction = 0.0
        if pos or neg:
            direction = (pos - neg) / (pos + neg)
        return EventResult(event_type, materiality, direction, novelty=1.0)


# --- Claude classifier ---

_PROMPT = """You classify a stock-market news item. Return ONLY compact JSON with keys:
event_type (one of earnings, guidance, litigation, product, macro, m&a, analyst, other),
materiality (integer 1-5, how market-moving), direction (float -1..1, bearish..bullish),
novelty (float 0..1, is this genuinely new vs a rehash).
Headline: {headline}
Body: {body}
JSON:"""


class ClaudeEventClassifier:
    def __init__(self, settings):
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._fallback = RuleEventClassifier()

    def classify(self, headline: str, body: str | None = None) -> EventResult:
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": _PROMPT.format(headline=headline, body=(body or "")[:1500]),
                }],
            )
            text = msg.content[0].text.strip()
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            et = str(data.get("event_type", "other")).lower()
            return EventResult(
                event_type=et if et in EVENT_TYPES else "other",
                materiality=int(max(1, min(5, data.get("materiality", 2)))),
                direction=float(max(-1.0, min(1.0, data.get("direction", 0.0)))),
                novelty=float(max(0.0, min(1.0, data.get("novelty", 1.0)))),
            )
        except Exception as exc:  # noqa: BLE001 - never fail the pipeline on the LLM
            log.warning("Claude event classify failed (%s); using rules", type(exc).__name__)
            return self._fallback.classify(headline, body)


class EventClassifier(Protocol):
    def classify(self, headline: str, body: str | None = None) -> EventResult: ...


def make_event_classifier(settings) -> EventClassifier:
    if settings.has_anthropic_key:
        try:
            return ClaudeEventClassifier(settings)
        except Exception:  # noqa: BLE001
            log.warning("anthropic client init failed; using rule-based classifier")
    return RuleEventClassifier()
