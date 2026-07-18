"""Command-line helpers. ``sentinel-backfill`` runs a one-off daily-bar
backfill for the whole watchlist (useful outside the worker loop)."""

from __future__ import annotations

import argparse

from sentinel.config import get_settings
from sentinel.ingestion.alpaca import AlpacaMarketData
from sentinel.ingestion.prices import backfill_prices
from sentinel.logging_config import configure_logging, get_logger

log = get_logger("sentinel.cli")


def backfill_command() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily bars for the watchlist")
    parser.add_argument(
        "--years", type=int, default=None, help="years of history (default: settings)"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-fetch even if bars already exist"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.has_alpaca_credentials:
        raise SystemExit("Alpaca credentials are required for backfill")

    watchlist = settings.load_watchlist()
    md = AlpacaMarketData(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        settings.alpaca_data_feed,
    )
    years = args.years or settings.backfill_years
    results = backfill_prices(md, watchlist, years, force=args.force)
    total = sum(results.values())
    log.info("done: %d bars written", total)


if __name__ == "__main__":
    backfill_command()
