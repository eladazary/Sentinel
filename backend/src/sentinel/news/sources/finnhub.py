"""Finnhub company-news source (free tier, needs an API key)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from sentinel.logging_config import get_logger
from sentinel.news.base import NewsItem

log = get_logger(__name__)

_URL = "https://finnhub.io/api/v1/company-news"


class FinnhubSource:
    name = "finnhub"

    def __init__(self, api_key: str | None):
        self._key = api_key

    def available(self) -> bool:
        return bool(self._key)

    def fetch(self, symbol: str, since: datetime, limit: int = 50) -> list[NewsItem]:
        if not self._key:
            return []
        params = {
            "symbol": symbol.upper(),
            "from": since.date().isoformat(),
            "to": datetime.now(timezone.utc).date().isoformat(),
            "token": self._key,
        }
        try:
            with httpx.Client() as client:
                r = client.get(_URL, params=params, timeout=15)
                r.raise_for_status()
                rows = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Finnhub fetch failed for %s: %s", symbol, exc)
            return []

        items: list[NewsItem] = []
        for row in rows[:limit]:
            ts = datetime.fromtimestamp(row.get("datetime", 0), tz=timezone.utc)
            if ts < since:
                continue
            items.append(
                NewsItem(
                    symbol=symbol.upper(),
                    ts=ts,
                    source=self.name,
                    headline=row.get("headline", ""),
                    summary=row.get("summary", ""),
                    url=row.get("url", ""),
                    external_id=f"finnhub:{row.get('id', '')}",
                )
            )
        return items
