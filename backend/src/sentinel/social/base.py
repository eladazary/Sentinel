"""Social post model and source protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class SocialPost:
    symbol: str
    ts: datetime
    source: str  # stocktwits | reddit | x
    author: str
    text: str
    engagement: float = 0.0  # likes/upvotes/comments proxy
    stance: float | None = None  # pre-labeled bull(+1)/bear(-1) if the source provides it
    external_id: str = ""
    url: str = ""
    # Filled by the pipeline:
    sentiment_score: float | None = None
    credibility: float | None = None
    impact: float | None = None

    def key(self) -> str:
        return self.external_id or f"{self.source}:{self.symbol}:{self.author}:{self.ts.isoformat()}"


class SocialSource(Protocol):
    name: str

    def available(self) -> bool: ...

    def fetch(self, symbol: str, limit: int = 50) -> list[SocialPost]: ...
