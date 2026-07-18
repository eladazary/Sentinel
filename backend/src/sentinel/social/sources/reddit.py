"""Reddit source via PRAW (needs a Reddit app: client id + secret).

Searches configured subreddits for recent submissions mentioning the ticker.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sentinel.logging_config import get_logger
from sentinel.social.base import SocialPost

log = get_logger(__name__)


class RedditSource:
    name = "reddit"

    def __init__(self, settings):
        self._settings = settings
        self._reddit = None

    def available(self) -> bool:
        return self._settings.has_reddit_credentials

    def _client(self):
        if self._reddit is None:
            import praw

            self._reddit = praw.Reddit(
                client_id=self._settings.reddit_client_id,
                client_secret=self._settings.reddit_client_secret,
                user_agent=self._settings.reddit_user_agent,
                check_for_async=False,
            )
            self._reddit.read_only = True
        return self._reddit

    def fetch(self, symbol: str, limit: int = 50) -> list[SocialPost]:
        if not self.available():
            return []
        posts: list[SocialPost] = []
        try:
            reddit = self._client()
            subs = "+".join(self._settings.reddit_subreddits)
            query = f"{symbol}"
            per_sub = max(1, limit // max(1, len(self._settings.reddit_subreddits)))
            for sub in reddit.subreddit(subs).search(
                query, sort="new", time_filter="week", limit=per_sub
            ):
                ts = datetime.fromtimestamp(sub.created_utc, tz=timezone.utc)
                posts.append(
                    SocialPost(
                        symbol=symbol.upper(),
                        ts=ts,
                        source=self.name,
                        author=str(sub.author) if sub.author else "unknown",
                        text=f"{sub.title}. {getattr(sub, 'selftext', '')}"[:2000],
                        engagement=float(sub.score + sub.num_comments),
                        external_id=f"reddit:{sub.id}",
                        url=f"https://reddit.com{sub.permalink}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Reddit fetch failed for %s: %s", symbol, exc)
        return posts
