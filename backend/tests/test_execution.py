"""Execution tests: sim broker, scheduler, and the trading loop (offline)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sentinel.config import Settings, Ticker, Watchlist
from sentinel.execution import loop as loop_mod
from sentinel.execution.scheduler import in_entry_window, is_market_open
from sentinel.execution.sim_broker import SimBroker
from sentinel.features.engineering import FEATURE_COLUMNS

ET = ZoneInfo("America/New_York")


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


@pytest.fixture
def patched_loop(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(loop_mod, "build_symbol_features", lambda s, w, sym, st: _feature_frame())
    monkeypatch.setattr(
        loop_mod, "load_bars",
        lambda s, sym: pd.DataFrame({"close": [100.0, 101.0, 100.0]}),
    )
    monkeypatch.setattr(loop_mod, "_day_start_and_hwm", lambda s, n, e: (e, e))
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
    return Settings(default_risk_factor=10)  # gate 35, easy to trigger BUY


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
