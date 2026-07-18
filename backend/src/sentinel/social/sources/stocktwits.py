"""StockTwits per-ticker stream (free public API, pre-labeled bull/bear)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from sentinel.logging_config import get_logger
from sentinel.social.base import SocialPost

log = get_logger(__name__)

_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"


class StockTwitsSource:
    name = "stocktwits"

    def available(self) -> bool:
        return True  # keyless

    def fetch(self, symbol: str, limit: int = 50) -> list[SocialPost]:
        try:
            with httpx.Client(headers={"User-Agent": "sentinel/0.1"}) as client:
                r = client.get(_URL.format(symbol=symbol.upper()), timeout=15)
                r.raise_for_status()
                messages = r.json().get("messages", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("StockTwits fetch failed for %s: %s", symbol, exc)
            return []

        posts: list[SocialPost] = []
        for m in messages[:limit]:
            try:
                ts = datetime.fromisoformat(
                    m["created_at"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (KeyError, ValueError):
                ts = datetime.now(timezone.utc)
            sentiment = (m.get("entities") or {}).get("sentiment") or {}
            basic = sentiment.get("basic")
            stance = 1.0 if basic == "Bullish" else -1.0 if basic == "Bearish" else None
            likes = (m.get("likes") or {}).get("total", 0)
            posts.append(
                SocialPost(
                    symbol=symbol.upper(),
                    ts=ts,
                    source=self.name,
                    author=(m.get("user") or {}).get("username", "unknown"),
                    text=m.get("body", ""),
                    engagement=float(likes),
                    stance=stance,
                    external_id=f"stocktwits:{m.get('id', '')}",
                )
            )
        return posts
