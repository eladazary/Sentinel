"""Ingestion worker entrypoint.

On startup: wait for the database, backfill 5 years of daily bars for the
watchlist, then poll the latest price for each ticker on a fixed interval and
publish a heartbeat to Redis. Phase 0 reads market data only — it never trades.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone

from sqlalchemy import text

from sentinel.config import get_settings
from sentinel.db import get_engine
from sentinel.ingestion.alpaca import AlpacaMarketData
from sentinel.ingestion.prices import backfill_prices, ingest_latest_prices
from sentinel.logging_config import configure_logging, get_logger
from sentinel.redis_client import get_redis

log = get_logger("sentinel.worker")

_running = True


def _handle_signal(signum, _frame) -> None:
    global _running
    log.info("received signal %s, shutting down after current cycle", signum)
    _running = False


def wait_for_db(timeout_seconds: int = 120) -> None:
    """Block until the database accepts connections (or raise on timeout)."""
    deadline = time.monotonic() + timeout_seconds
    engine = get_engine()
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("database is ready")
            return
        except Exception as exc:  # noqa: BLE001 - startup retry loop
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for database") from exc
            log.info("waiting for database... (%s)", type(exc).__name__)
            time.sleep(2)


def _heartbeat(reason: str) -> None:
    try:
        get_redis().set(
            "sentinel:worker:heartbeat",
            datetime.now(timezone.utc).isoformat(),
        )
        get_redis().set("sentinel:worker:status", reason)
    except Exception:  # noqa: BLE001 - Redis is best-effort here
        pass


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("worker starting in %s mode", settings.mode)

    watchlist = settings.load_watchlist()
    log.info("watchlist: %s", ", ".join(watchlist.symbols))

    wait_for_db()

    if not settings.has_alpaca_credentials:
        log.warning(
            "ALPACA credentials not set — ingestion is idle. Set "
            "SENTINEL_ALPACA_API_KEY / SENTINEL_ALPACA_SECRET_KEY to enable "
            "price backfill and live updates."
        )
        while _running:
            _heartbeat("idle:no-credentials")
            time.sleep(settings.ingest_interval_seconds)
        return

    md = AlpacaMarketData(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        settings.alpaca_data_feed,
    )

    try:
        backfill_prices(md, watchlist, settings.backfill_years)
    except Exception:  # noqa: BLE001 - never crash the loop on a backfill error
        log.exception("backfill failed; continuing to live polling")

    log.info(
        "entering live polling loop (every %ds)", settings.ingest_interval_seconds
    )
    while _running:
        try:
            ingest_latest_prices(md, watchlist.symbols)
            _heartbeat("ok")
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("latest-price ingestion cycle failed")
            _heartbeat("error")
        # Sleep in short slices so signals are handled promptly.
        for _ in range(settings.ingest_interval_seconds):
            if not _running:
                break
            time.sleep(1)

    log.info("worker stopped")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run()


if __name__ == "__main__":
    main()
