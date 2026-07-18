"""News item model and source protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class NewsItem:
    symbol: str
    ts: datetime
    source: str
    headline: str
    summary: str = ""
    url: str = ""
    external_id: str = ""
    # Filled in by the pipeline after scoring:
    event_type: str | None = None
    materiality: int | None = None
    direction: float | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    impact: float | None = None  # per-item signed impact after weighting/decay

    def key(self) -> str:
        """Stable dedup key across sources."""
        return self.external_id or f"{self.source}:{self.symbol}:{self.headline}"


class NewsSource(Protocol):
    name: str

    def available(self) -> bool: ...

    def fetch(self, symbol: str, since: datetime, limit: int = 50) -> list[NewsItem]: ...
