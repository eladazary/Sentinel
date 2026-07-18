"""Small Redis helper. Used for health checks and a worker heartbeat / price
cache. Kept optional so the API degrades gracefully if Redis is unavailable."""

from __future__ import annotations

from functools import lru_cache

import redis

from sentinel.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, decode_responses=True, socket_connect_timeout=2
    )


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
