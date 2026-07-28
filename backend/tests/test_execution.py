"""Execution tests: sim broker, scheduler, and the trading loop (offline)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sentinel.config import Settings, Ticker, Watchlist
from sentinel.execution import loop as loop_mod
from sentinel.execution.factory import (
    BrokerUnavailable,
    make_broker_with_status,
    reset_broker_cache,
)
from sentinel.execution.scheduler import in_entry_window, is_market_open
from sentinel.execution.sim_broker import SimBroker
from sentinel.features.engineering import FEATURE_COLUMNS

ET = ZoneInfo("America/New_York")


# ---- broker factory ----

@pytest.fixture(autouse=True)
def _clean_broker_cache():
    reset_broker_cache()
    yield
    reset_broker_cache()


@pytest.fixture
def rejected_alpaca(monkeypatch):
    """Credentials that are present but answered with a 401."""
    import sentinel.execution.alpaca_broker as alpaca_mod

    class _Rejecting:
        def __init__(self, *a, **kw):
            pass

        def get_account(self):
            raise RuntimeError("401 Client Error: Unauthorized for url: /v2/account")

    monkeypatch.setattr(alpaca_mod, "AlpacaBroker", _Rejecting)


def _creds(**kw):
    return Settings(alpaca_api_key="PKTEST", alpaca_secret_key="secret", **kw)


def _no_creds(**kw):
    # Explicit, so a stray SENTINEL_ALPACA_* in the environment can't turn these
    # into live network calls.
    return Settings(alpaca_api_key=None, alpaca_secret_key=None, **kw)


def test_dry_run_degrades_to_sim_on_bad_credentials(rejected_alpaca):
    broker, status = make_broker_with_status(_creds(mode="DRY_RUN"))
    assert isinstance(broker, SimBroker)
    assert status.broker == "sim" and status.degraded
    assert "401" in status.detail


def test_live_refuses_to_simulate_on_bad_credentials(rejected_alpaca):
    with pytest.raises(BrokerUnavailable):
        make_broker_with_status(_creds(mode="LIVE"))


def test_live_refuses_without_credentials():
    with pytest.raises(BrokerUnavailable):
        make_broker_with_status(_no_creds(mode="LIVE"))


def test_sim_is_reused_so_positions_survive():
    s = _no_creds(mode="DRY_RUN")
    first, _ = make_broker_with_status(s)
    first.submit_bracket("AAA", 10, 100.0, 95.0, 110.0)
    second, _ = make_broker_with_status(s)
    assert second is first
    assert "AAA" in second.get_positions()


# ---- sim broker ----

def test_sim_broker_bracket_and_close():
    b = SimBroker(cash=100_000)
    b.set_prices({"AAA": 100.0})
    res = b.submit_bracket("AAA", 100, 100.0, 95.0, 110.0)
    assert res.status == "filled"
    assert b.get_account().equity == pytest.approx(100_000, abs=1)
    assert "AAA" in b.get_positions()
    b.close_position("AAA")
    assert "AAA" not in b.get_positions()


def test_sim_broker_mark_triggers_stop():
    b = SimBroker(cash=100_000)
    b.submit_bracket("AAA", 100, 100.0, 95.0, 110.0)
    closed = b.mark({"AAA": 94.0})  # below stop
    assert closed == ["AAA"]
    assert "AAA" not in b.get_positions()


# ---- scheduler ----

def test_market_hours():
    # Wednesday 11:00 ET -> open and within entry window.
    dt = datetime(2026, 7, 15, 11, 0, tzinfo=ET)
    assert is_market_open(dt)
    assert in_entry_window(dt)


def test_entry_window_excludes_open_and_close():
    assert not in_entry_window(datetime(2026, 7, 15, 9, 45, tzinfo=ET))  # too early
    assert not in_entry_window(datetime(2026, 7, 15, 15, 45, tzinfo=ET))  # too late


def test_weekend_closed():
    assert not is_market_open(datetime(2026, 7, 18, 12, 0, tzinfo=ET))  # Saturday


# ---- trading loop (monkeypatched DB + data) ----

class FakeModel:
    trained_through = "2024-12-31"

    def __init__(self, prob):
        self._p = prob

    def predict_one(self, features):
        from sentinel.model.technical import prob_to_confidence, prob_to_score

        return self._p, prob_to_score(self._p), prob_to_confidence(self._p)


@contextmanager
def _fake_session():
    yield SimpleNamespace(name="fake")


def _feature_frame():
    idx = pd.date_range("2024-01-01", periods=3, freq="B", tz="UTC")
    data = {c: np.linspace(0.1, 0.2, 3) for c in FEATURE_COLUMNS}
    data["atr_pct"] = [0.02, 0.02, 0.02]
    data["rsi14"] = [55, 60, 65]
    return pd.DataFrame(data, index=idx)


def _quote(price, age_seconds=0.0, source="alpaca"):
    """A LatestPrice-shaped row.

    Entries require a quote that is both fresh and from a real-time feed, so the
    source matters as much as the age.
    """
    return SimpleNamespace(
        price=price,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        source=source,
    )


@pytest.fixture
def patched_loop(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(loop_mod, "build_symbol_features", lambda s, w, sym, st: _feature_frame())
    monkeypatch.setattr(
        loop_mod, "load_bars",
        lambda s, sym: pd.DataFrame({"close": [100.0, 101.0, 100.0]}),
    )
    monkeypatch.setattr(loop_mod, "_day_start_and_hwm", lambda s, n, e: (e, e))
    monkeypatch.setattr(loop_mod, "get_sentiment_cache", lambda s, syms: {})
    # Fresh quotes by default: the entry path prices off the live quote.
    monkeypatch.setattr(
        loop_mod, "get_latest_prices",
        lambda s, syms: {sym: _quote(100.0) for sym in syms},
    )
    monkeypatch.setattr(loop_mod, "is_in_blackout", lambda s, sym, n, h: False)
    monkeypatch.setattr(loop_mod, "ensure_dry_run_started", lambda s, n=None: None)
    monkeypatch.setattr(loop_mod, "record_breaker_event", lambda s, **kw: None)
    monkeypatch.setattr(
        loop_mod.dlog, "log_decision",
        lambda session, **kw: logged.append({"action": kw["action"], "symbol": kw["symbol"]}),
    )
    monkeypatch.setattr(loop_mod.dlog, "upsert_signal_snapshot", lambda session, **kw: None)
    monkeypatch.setattr(loop_mod.dlog, "record_equity", lambda session, **kw: None)
    return logged


def _watchlist():
    return Watchlist(tickers=[Ticker(symbol="AAA", name="Alpha", sector_etf="XLK")])


def _settings():
    return Settings(default_risk_factor=10)  # most permissive gate, easy to fire BUY


def test_loop_opens_position(patched_loop):
    broker = SimBroker(cash=100_000)
    broker.set_prices({"AAA": 100.0})
    report = loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.95), enforce_entry_window=False,
    )
    assert any(a["action"] == "OPEN" for a in report.actions)
    assert "AAA" in broker.get_positions()
    assert any(d["action"] == "OPEN" for d in patched_loop)


def test_loop_skips_below_gate(patched_loop):
    broker = SimBroker(cash=100_000)
    broker.set_prices({"AAA": 100.0})
    report = loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.52), enforce_entry_window=False,  # weak signal
    )
    assert any(a["action"] == "SKIP" for a in report.actions)
    assert "AAA" not in broker.get_positions()


def test_loop_marks_sim_to_market(patched_loop, monkeypatch):
    """A cycle must mark the sim, or equity freezes at the entry price forever."""
    broker = SimBroker(cash=100_000)
    broker.submit_bracket("AAA", 100, 100.0, 95.0, 110.0)
    monkeypatch.setattr(
        loop_mod, "get_latest_prices",
        lambda s, syms: {"AAA": _quote(94.0)},  # below the stop
    )
    loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.20), enforce_entry_window=False,
    )
    assert "AAA" not in broker.get_positions()  # stop triggered on the mark
    assert broker.get_account().equity == pytest.approx(99_400, abs=1)


def test_loop_breaker_flattens(patched_loop, monkeypatch):
    broker = SimBroker(cash=100_000)
    broker.submit_bracket("AAA", 100, 100.0, 95.0, 110.0)
    broker.set_prices({"AAA": 100.0})
    # Force a daily loss well past -3% by faking a high day-start equity.
    monkeypatch.setattr(loop_mod, "_day_start_and_hwm", lambda s, n, e: (e * 1.10, e * 1.10))
    report = loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.95), enforce_entry_window=False,
    )
    assert report.breaker_tripped
    assert broker.get_positions() == {}


# ---- market-hours gating of the loop ----

def test_market_open_boundaries():
    """What the worker uses to decide whether to run a cycle at all."""
    # Wednesday 2026-07-22.
    wed = datetime(2026, 7, 22, 9, 29, tzinfo=ET)
    assert is_market_open(wed) is False               # 09:29 — before the open
    assert is_market_open(wed.replace(hour=9, minute=30)) is True
    assert is_market_open(wed.replace(hour=15, minute=59)) is True
    assert is_market_open(wed.replace(hour=16, minute=0)) is False  # closed at 16:00

    # Saturday 2026-07-25 — closed at every hour.
    sat = datetime(2026, 7, 25, 12, 0, tzinfo=ET)
    assert sat.weekday() == 5
    assert is_market_open(sat) is False
    assert is_market_open(sat.replace(hour=20)) is False


def test_cycle_records_equity_but_saturday_is_closed(patched_loop, monkeypatch):
    """The worker gates run_cycle on is_market_open, so a shut market records
    nothing — that's what invented weekend 'trading days' in the go-live gate."""
    recorded = []
    monkeypatch.setattr(
        loop_mod.dlog, "record_equity",
        lambda session, **kw: recorded.append(kw["ts"]),
    )
    broker = SimBroker(cash=100_000)
    broker.set_prices({"AAA": 100.0})

    loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.52), enforce_entry_window=False,
    )
    assert len(recorded) == 1  # a cycle that does run still records equity

    saturday = datetime(2026, 7, 25, 12, 0, tzinfo=ET)
    assert is_market_open(saturday) is False  # so the worker never calls it


# ---- entry pricing: live quote, never the stale daily close ----

def test_entry_prices_off_the_live_quote_not_the_daily_close(patched_loop, monkeypatch):
    """The bug: limits built on a days-old close land below the market."""
    submitted = {}

    class RecordingBroker(SimBroker):
        def submit_bracket(self, symbol, qty, limit_price, stop_price, take_profit):
            submitted.update(
                symbol=symbol, qty=qty, limit=limit_price,
                stop=stop_price, tp=take_profit,
            )
            return super().submit_bracket(symbol, qty, limit_price, stop_price, take_profit)

    broker = RecordingBroker(cash=100_000)
    # Daily close is 100 (from the fixture); the market has since moved to 130.
    monkeypatch.setattr(
        loop_mod, "get_latest_prices", lambda s, syms: {"AAA": _quote(130.0)}
    )
    loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.95), enforce_entry_window=False,
    )
    # Priced off 130, not 100 — and above it, so it can actually fill.
    assert submitted["limit"] > 130.0
    assert submitted["limit"] == pytest.approx(130.0 * 1.0025, abs=0.01)
    # Stop and target hang off the real entry price too.
    assert submitted["stop"] < 130.0 < submitted["tp"]


def test_entry_is_refused_on_a_stale_quote(patched_loop, monkeypatch):
    """Better no trade than a limit priced on a quote we can't vouch for."""
    monkeypatch.setattr(
        loop_mod, "get_latest_prices",
        lambda s, syms: {"AAA": _quote(130.0, age_seconds=9_999)},
    )
    broker = SimBroker(cash=100_000)
    report = loop_mod.run_cycle(
        session_factory=_fake_session, settings=_settings(), watchlist=_watchlist(),
        broker=broker, model=FakeModel(0.95), enforce_entry_window=False,
    )
    assert "AAA" not in broker.get_positions()
    assert any(a["reason"] == "unusable quote" for a in report.actions)


def test_unfillable_resting_buy_is_cancelled_not_left_to_block(monkeypatch):
    """A buy limit below the market can't fill but still blocks the symbol."""
    from sentinel.execution.broker import WorkingOrder

    cancelled = []

    class StuckBroker(SimBroker):
        def open_order_keys(self):
            return {("AAA", "buy")}

        def open_orders(self):
            return [WorkingOrder(
                id="o1", symbol="AAA", qty=10, side="buy",
                status="new", limit_price=100.0,
            )]

        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return True

    freed = loop_mod._cancel_unfillable_buys(
        StuckBroker(cash=100_000),
        {"AAA": loop_mod.Quote(price=130.0, age_seconds=1.0, source="alpaca")},  # market ran away
        _settings(),
    )
    assert cancelled == ["o1"]
    assert freed == {("AAA", "buy")}


def test_a_still_viable_resting_buy_is_left_alone():
    from sentinel.execution.broker import WorkingOrder

    cancelled = []

    class Broker(SimBroker):
        def open_orders(self):
            return [WorkingOrder(
                id="o1", symbol="AAA", qty=10, side="buy",
                status="new", limit_price=131.0,  # at/above the market
            )]

        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return True

    freed = loop_mod._cancel_unfillable_buys(
        Broker(cash=100_000),
        {"AAA": loop_mod.Quote(price=130.0, age_seconds=1.0, source="alpaca")},
        _settings(),
    )
    assert cancelled == [] and freed == set()
