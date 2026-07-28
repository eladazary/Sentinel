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

from sentinel.alerts.notifier import make_notifier
from sentinel.config import get_settings
from sentinel.db import get_engine, session_scope
from sentinel.execution.factory import make_broker_with_status
from sentinel.execution.loop import run_cycle
from sentinel.execution.scheduler import is_market_open
from sentinel.ingestion.prices import (
    backfill_symbols,
    ingest_latest_prices,
    make_market_data,
    make_quote_source,
)
from sentinel.logging_config import configure_logging, get_logger
from sentinel.model.technical import TechnicalModel
from sentinel.nlp.events import make_event_classifier
from sentinel.nlp.sentiment import make_sentiment_scorer
from sentinel.redis_client import get_redis
from sentinel.sentiment_jobs import refresh_sentiment
from sentinel.universe import load_universe, mark_backfilled, pending_backfill

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


def _sync_universe(settings, md, watchlist, symbols):
    """Reload the universe and backfill any ticker that has no history yet.

    Returns the (possibly unchanged) watchlist and ingest-symbol list. A new
    ticker needs its full bar history before it can produce features, so the
    backfill happens here rather than waiting for a worker restart.
    """
    with session_scope() as session:
        fresh = load_universe(session, settings)
        todo = [t.symbol for t in pending_backfill(session)]

    if todo:
        log.info("backfilling %d new ticker(s): %s", len(todo), ", ".join(todo))
        for symbol in todo:
            try:
                backfill_symbols(md, [symbol], settings.backfill_years)
            except Exception:  # noqa: BLE001 - one bad symbol must not stall the rest
                log.exception("backfill failed for %s; will retry next cycle", symbol)
                continue
            with session_scope() as session:
                mark_backfilled(session, symbol)

    if fresh.symbols != watchlist.symbols:
        log.info("watchlist changed: %s", ", ".join(fresh.symbols))
        return fresh, settings.all_ingest_symbols(fresh)
    return watchlist, symbols


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("worker starting in %s mode", settings.mode)

    wait_for_db()

    # The universe lives in the DB (seeded from YAML on first use) so it can be
    # edited from the UI without a restart.
    with session_scope() as session:
        watchlist = load_universe(session, settings)
    log.info("watchlist: %s", ", ".join(watchlist.symbols))

    # All ingest symbols = watchlist + benchmark/sector/VIX context.
    symbols = settings.all_ingest_symbols(watchlist)
    md = make_market_data(settings)
    # Quotes come from their own provider: backfill wants years of free daily
    # bars, whereas order pricing needs a live trade feed.
    quote_md, quote_name = make_quote_source(settings)
    log.info(
        "backfill via %s; live quotes via %s",
        settings.backfill_source, quote_name,
    )
    try:
        backfill_symbols(md, symbols, settings.backfill_years)
    except Exception:  # noqa: BLE001 - never crash the loop on a backfill error
        log.exception("backfill failed; continuing")

    model = _ensure_model(settings)  # loads, or auto-trains if data exists
    broker, broker_status = make_broker_with_status(settings)
    if broker_status.degraded:
        # Loud, because every equity number downstream is now simulated.
        log.warning(
            "BROKER DEGRADED — %s. Running the paper loop on the in-memory sim; "
            "fix the credentials and restart the worker to reconnect.",
            broker_status.detail,
        )
    notifier = make_notifier(settings)

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
    was_open: bool | None = None
    while _running:
        # Pick up tickers added through the API, and give each one its price
        # history before the model is asked to score it.
        try:
            watchlist, symbols = _sync_universe(settings, md, watchlist, symbols)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("universe sync failed")

        # Prices and the trading cycle are session-only. Quotes don't move when
        # the market is shut, and a cycle run then would record an equity
        # snapshot for a day no order could be placed on — which is what used to
        # inflate the go-live gate's trading-day count with weekends.
        market_open = is_market_open(datetime.now(timezone.utc))
        if market_open is not was_open:
            log.info("market %s — %s", "open" if market_open else "closed",
                     "resuming price ingest + trading cycle" if market_open
                     else "pausing price ingest + trading cycle (sentiment continues)")
            was_open = market_open

        if market_open:
            try:
                ingest_latest_prices(quote_md, symbols, source=quote_name)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("latest-price ingestion failed")

        # Sentiment keeps running regardless: filings and retail chatter arrive
        # outside session hours, and weekends are when chatter peaks.
        if cycle_count % refresh_every == 0:
            try:
                refresh_sentiment(
                    session_factory=session_scope, settings=settings,
                    watchlist=watchlist, sentiment=sentiment_scorer,
                    classifier=event_classifier,
                )
            except Exception:  # noqa: BLE001
                log.exception("sentiment refresh failed")

        if not market_open:
            _heartbeat("ok:market-closed")
        elif model is not None:
            try:
                report = run_cycle(
                    session_factory=session_scope,
                    settings=settings,
                    watchlist=watchlist,
                    broker=broker,
                    model=model,
                    enforce_entry_window=True,  # no off-hours order placement
                    notifier=notifier,
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
        return None


def _ensure_model(settings) -> TechnicalModel | None:
    """Load the model, or auto-train one if bars exist but no model is saved,
    so a fresh `docker compose up` needs no manual training step."""
    model = _load_model(settings)
    if model is not None:
        return model
    log.info("no saved model — attempting auto-train from stored bars")
    try:
        from sentinel.features.dataset import build_training_frame
        from sentinel.features.engineering import FEATURE_COLUMNS

        watchlist = settings.load_watchlist()
        with session_scope() as session:
            frame = build_training_frame(session, watchlist, settings)
        if frame.empty:
            log.warning("no training data yet — signals disabled until data + train")
            return None
        trained_through = str(frame.index.max().date())
        model = TechnicalModel.train(
            frame[FEATURE_COLUMNS], frame["label"], trained_through=trained_through
        )
        model.save(settings.model_dir)
        log.info("auto-trained model on %d rows through %s", len(frame), trained_through)
        return model
    except Exception:  # noqa: BLE001 - never block startup on training
        log.exception("auto-train failed; signals disabled")
        return None


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run()


if __name__ == "__main__":
    main()
