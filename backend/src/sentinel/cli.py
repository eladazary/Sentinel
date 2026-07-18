"""Command-line entrypoints.

* ``sentinel-backfill`` — backfill daily bars (watchlist + context symbols).
* ``sentinel-train``    — train the technical model on stored bars, save artifact.
* ``sentinel-backtest`` — run a walk-forward backtest and store the results.
* ``sentinel-cycle``    — run one trading cycle now (paper/sim).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sentinel.backtest.runner import run_backtest
from sentinel.config import get_settings
from sentinel.db import session_scope
from sentinel.execution.factory import make_broker
from sentinel.execution.loop import run_cycle
from sentinel.features.dataset import build_training_frame
from sentinel.features.engineering import FEATURE_COLUMNS
from sentinel.ingestion.prices import backfill_symbols, make_market_data
from sentinel.logging_config import configure_logging, get_logger
from sentinel.model.technical import TechnicalModel
from sentinel.model.walkforward import walk_forward_predict
from sentinel.models import BacktestRun

log = get_logger("sentinel.cli")


def backfill_command() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily bars (watchlist + context)")
    parser.add_argument("--years", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    watchlist = settings.load_watchlist()
    md = make_market_data(settings)
    symbols = settings.all_ingest_symbols(watchlist)
    years = args.years or settings.backfill_years
    results = backfill_symbols(md, symbols, years, force=args.force)
    log.info("backfill done: %d bars across %d symbols", sum(results.values()), len(results))


def train_command() -> None:
    parser = argparse.ArgumentParser(description="Train the technical model")
    parser.add_argument("--rounds", type=int, default=400)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    watchlist = settings.load_watchlist()

    with session_scope() as session:
        frame = build_training_frame(session, watchlist, settings)

    if frame.empty:
        raise SystemExit("no training data — run sentinel-backfill first")

    X, y = frame[FEATURE_COLUMNS], frame["label"]
    trained_through = str(frame.index.max().date())
    model = TechnicalModel.train(
        X, y, num_boost_round=args.rounds, trained_through=trained_through
    )
    path = model.save(settings.model_dir)
    log.info("trained on %d rows through %s -> %s", len(X), trained_through, path)

    # Report out-of-sample quality.
    wf = walk_forward_predict(
        frame, FEATURE_COLUMNS,
        train_days=settings.walkforward_train_days,
        step_days=settings.walkforward_step_days,
    )
    auc = wf.auc()
    log.info(
        "walk-forward: %d folds, %d predictions, AUC=%s, acc=%.3f",
        wf.n_folds, wf.n_predicted, f"{auc:.3f}" if auc else "n/a", wf.accuracy(),
    )
    top = sorted(model.feature_importance().items(), key=lambda kv: kv[1], reverse=True)[:5]
    log.info("top features: %s", ", ".join(f"{k}={v:.0f}" for k, v in top))


def backtest_command() -> None:
    parser = argparse.ArgumentParser(description="Run a walk-forward backtest")
    parser.add_argument("--risk", type=int, default=None, help="risk factor 1-10")
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    watchlist = settings.load_watchlist()

    with session_scope() as session:
        result, wf = run_backtest(session, watchlist, settings, risk_factor=args.risk)
        summary = result.summary()
        auc = wf.auc()
        if not args.no_store:
            session.add(
                BacktestRun(
                    created_at=datetime.now(timezone.utc),
                    risk_factor=result.config.get("risk_factor", 5),
                    start_date=summary["start"],
                    end_date=summary["end"],
                    n_trades=summary["n_trades"],
                    wf_auc=auc,
                    metrics=result.metrics.as_dict(),
                    benchmarks={k: v.as_dict() for k, v in result.benchmarks.items()},
                    config=result.config,
                )
            )

    print(json.dumps({"walk_forward_auc": auc, **summary}, indent=2))


def simulate_dryrun_command() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the last N months through the loop into the live tables"
    )
    parser.add_argument("--months", type=int, default=3)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    watchlist = settings.load_watchlist()
    from sentinel.golive.simulate import simulate_dryrun

    with session_scope() as session:
        summary = simulate_dryrun(session, watchlist, settings, months=args.months)
    print(json.dumps(summary, indent=2))


def cycle_command() -> None:
    parser = argparse.ArgumentParser(description="Run one trading cycle now")
    parser.add_argument("--risk", type=int, default=None)
    parser.add_argument(
        "--force", action="store_true", help="ignore the 10:00–15:30 ET entry window"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    watchlist = settings.load_watchlist()
    try:
        model = TechnicalModel.load(settings.model_dir)
    except Exception:
        model = None
        log.warning("no trained model found in %s; signals will be skipped", settings.model_dir)

    broker = make_broker(settings)
    report = run_cycle(
        session_factory=session_scope,
        settings=settings,
        watchlist=watchlist,
        broker=broker,
        model=model,
        risk_factor=args.risk,
        enforce_entry_window=not args.force,
    )
    print(json.dumps(
        {"ran_at": report.ran_at.isoformat(), "equity": report.equity,
         "breaker_tripped": report.breaker_tripped, "actions": report.actions},
        indent=2,
    ))


if __name__ == "__main__":
    backfill_command()
