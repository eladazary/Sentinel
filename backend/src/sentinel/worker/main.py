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
from sentinel.db import get_engine, session_scope
from sentinel.execution.factory import make_broker
from sentinel.execution.loop import run_cycle
from sentinel.ingestion.prices import (
    backfill_symbols,
    ingest_latest_prices,
    make_market_data,
)
from sentinel.logging_config import configure_logging, get_logger
from sentinel.model.technical import TechnicalModel
from sentinel.nlp.events import make_event_classifier
from sentinel.nlp.sentiment import make_sentiment_scorer
from sentinel.redis_client import get_redis
from sentinel.sentiment_jobs import refresh_sentiment

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

    # Backfill source is yfinance by default (free, no keys); Alpaca is used for
    # execution. All ingest symbols = watchlist + benchmark/sector/VIX context.
    symbols = settings.all_ingest_symbols(watchlist)
    md = make_market_data(settings)
    log.info("backfilling %d symbols via %s", len(symbols), settings.backfill_source)
    try:
        backfill_symbols(md, symbols, settings.backfill_years)
    except Exception:  # noqa: BLE001 - never crash the loop on a backfill error
        log.exception("backfill failed; continuing")

    model = _load_model(settings)
    broker = make_broker(settings)

    # FinBERT + event classifier are built once (FinBERT load is expensive).
    sentiment_scorer = make_sentiment_scorer(settings)
    event_classifier = make_event_classifier(settings)
    # Refresh news/social on a slower cadence than the trading loop (rate limits).
    refresh_every = max(1, 300 // settings.ingest_interval_seconds)
    cycle_count = 0

    log.info(
        "entering loop (every %ds): ingest prices → [sentiment] → signals → paper cycle",
        settings.ingest_interval_seconds,
    )
    while _running:
        try:
            ingest_latest_prices(md, symbols)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("latest-price ingestion failed")

        if cycle_count % refresh_every == 0:
            try:
                refresh_sentiment(
                    session_factory=session_scope, settings=settings,
                    watchlist=watchlist, sentiment=sentiment_scorer,
                    classifier=event_classifier,
                )
            except Exception:  # noqa: BLE001
                log.exception("sentiment refresh failed")

        if model is not None:
            try:
                report = run_cycle(
                    session_factory=session_scope,
                    settings=settings,
                    watchlist=watchlist,
                    broker=broker,
                    model=model,
                    enforce_entry_window=True,  # no off-hours order placement
                )
                _heartbeat(f"ok:{len(report.actions)} decisions")
            except Exception:  # noqa: BLE001
                log.exception("trading cycle failed")
                _heartbeat("error:cycle")
        else:
            _heartbeat("ok:no-model (run sentinel-train)")

        cycle_count += 1
        for _ in range(settings.ingest_interval_seconds):
            if not _running:
                break
            time.sleep(1)

    log.info("worker stopped")


def _load_model(settings) -> TechnicalModel | None:
    try:
        model = TechnicalModel.load(settings.model_dir)
        log.info("loaded technical model (trained through %s)", model.trained_through)
        return model
    except Exception:  # noqa: BLE001 - model is optional until trained
        log.warning(
            "no technical model in %s — signals disabled until `sentinel-train` runs",
            settings.model_dir,
        )
        return None


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run()


if __name__ == "__main__":
    main()
