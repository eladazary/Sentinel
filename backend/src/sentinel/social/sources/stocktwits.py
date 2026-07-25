"""StockTwits per-ticker stream (undocumented public API, pre-labeled bull/bear).

This endpoint now sits behind a Cloudflare bot challenge (403 with
``cf-mitigated: challenge``). No header combination gets through it — passing the
challenge needs JS execution or an authenticated StockTwits token. When that
happens the source parks itself for ``_COOLDOWN`` rather than retrying every
refresh, since a blocked client re-asking 84 times an hour only deepens the
block. Configure Reddit credentials for a working social signal in the meantime.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from sentinel.logging_config import get_logger
from sentinel.social.base import SocialPost

log = get_logger(__name__)

_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
# Long enough that a temporary block gets a chance to lapse, short enough that
# recovery doesn't need a restart.
_COOLDOWN_SECONDS = 3600.0
# A browser-ish UA is still the best odds when the challenge isn't active.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _is_bot_challenge(resp: httpx.Response) -> bool:
    """Cloudflare interstitial rather than an API rejection we can act on."""
    if "cf-mitigated" in resp.headers:
        return True
    ctype = resp.headers.get("content-type", "")
    return resp.status_code in (403, 503) and "html" in ctype.lower()


class StockTwitsSource:
    name = "stocktwits"

    def __init__(self) -> None:
        self._blocked_until = 0.0
        self._logged_block = False

    def available(self) -> bool:
        """Keyless, but unavailable while parked behind the bot challenge."""
        return time.monotonic() >= self._blocked_until

    def _park(self, symbol: str) -> None:
        self._blocked_until = time.monotonic() + _COOLDOWN_SECONDS
        if not self._logged_block:
            log.error(
                "StockTwits is behind a Cloudflare bot challenge (blocked on %s); "
                "pausing this source for %.0f min. Header tweaks can't pass it — "
                "it needs an authenticated StockTwits token, or configure Reddit "
                "(SENTINEL_REDDIT_CLIENT_ID/SECRET) as the social source instead.",
                symbol, _COOLDOWN_SECONDS / 60.0,
            )
            self._logged_block = True

    def fetch(self, symbol: str, limit: int = 50) -> list[SocialPost]:
        if not self.available():
            return []
        try:
            with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
                r = client.get(_URL.format(symbol=symbol.upper()), timeout=15)
                if _is_bot_challenge(r):
                    self._park(symbol)
                    return []
                r.raise_for_status()
                messages = r.json().get("messages", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("StockTwits fetch failed for %s: %s", symbol, exc)
            return []

        # Got through — clear any prior block so recovery is automatic.
        self._blocked_until = 0.0
        self._logged_block = False

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
