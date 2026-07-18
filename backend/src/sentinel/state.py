"""Runtime, operator-adjustable state (Redis-backed, with safe fallbacks).

Currently just the active Risk Factor — the dial in the UI writes here and the
trading loop reads it, so a change takes effect on the next cycle without a
redeploy. Falls back to the configured default if Redis is unavailable.
"""

from __future__ import annotations

from sentinel.logging_config import get_logger
from sentinel.redis_client import get_redis

log = get_logger(__name__)

_RISK_KEY = "sentinel:risk_factor"


def get_risk_factor(default: int) -> int:
    try:
        v = get_redis().get(_RISK_KEY)
        if v is not None:
            return max(1, min(10, int(v)))
    except Exception:  # noqa: BLE001 - Redis optional
        pass
    return default


def set_risk_factor(value: int) -> int:
    value = max(1, min(10, int(value)))
    try:
        get_redis().set(_RISK_KEY, value)
    except Exception:  # noqa: BLE001
        log.warning("could not persist risk factor to Redis")
    return value
