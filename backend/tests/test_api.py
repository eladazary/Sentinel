"""API endpoint tests. Infra (DB/Redis) is faked so these run offline."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.api.deps import get_db, watchlist_dep
from sentinel.api.routers import health as health_mod
from sentinel.api.routers import watchlist as watchlist_mod
from sentinel.config import Ticker, Watchlist
from sentinel.repositories import WatchlistRow


# ---- health ----

class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return None


class _FakeEngine:
    def connect(self):
        return _FakeConn()


def test_health_ok(client, monkeypatch):
    monkeypatch.setattr(health_mod, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(health_mod, "redis_ping", lambda: True)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] in {"DRY_RUN", "LIVE"}
    names = {c["name"]: c["ok"] for c in body["components"]}
    assert names["database"] is True
    assert names["redis"] is True
    # The broker is reported for visibility but must not gate liveness.
    assert any(n.startswith("broker") for n in names)


def test_health_degraded_returns_503(client, monkeypatch):
    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("no db"))
    )
    monkeypatch.setattr(health_mod, "redis_ping", lambda: False)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    comps = {c["name"]: c["ok"] for c in body["components"]}
    assert comps["database"] is False
    assert comps["redis"] is False


def test_degraded_broker_does_not_fail_health(client, monkeypatch):
    """A rejected key must not take the API container down with it."""
    from sentinel.execution.factory import BrokerStatus

    monkeypatch.setattr(health_mod, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(health_mod, "redis_ping", lambda: True)
    monkeypatch.setattr(
        "sentinel.execution.factory.make_broker_with_status",
        lambda s: (None, BrokerStatus("sim", degraded=True, detail="HTTP 401")),
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    broker = next(c for c in body["components"] if c["name"].startswith("broker"))
    assert broker["ok"] is False and broker["detail"] == "HTTP 401"


# ---- watchlist ----

@pytest.fixture
def wl_override(app):
    wl = Watchlist(
        tickers=[
            Ticker(symbol="NVDA", name="NVIDIA"),
            Ticker(symbol="MSFT", name="Microsoft"),
        ]
    )
    app.dependency_overrides[watchlist_dep] = lambda: wl
    app.dependency_overrides[get_db] = lambda: object()
    yield wl
    app.dependency_overrides.clear()


def test_watchlist_shape_and_change(client, wl_override, monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        WatchlistRow(
            symbol="NVDA",
            name="NVIDIA",
            price=110.0,
            prev_close=100.0,
            change=10.0,
            change_pct=10.0,
            as_of=now,  # fresh
            spark=[100.0, 105.0, 110.0],
        ),
        WatchlistRow(
            symbol="MSFT",
            name="Microsoft",
            price=None,
            prev_close=None,
            change=None,
            change_pct=None,
            as_of=None,  # never seen -> stale
            spark=[],
        ),
    ]
    monkeypatch.setattr(watchlist_mod, "build_watchlist_rows", lambda db, tickers: rows)

    resp = client.get("/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    nvda, msft = body["tickers"]
    assert nvda["symbol"] == "NVDA"
    assert nvda["change_pct"] == 10.0
    assert nvda["stale"] is False
    assert nvda["spark"] == [100.0, 105.0, 110.0]
    assert msft["price"] is None
    assert msft["stale"] is True


def test_watchlist_marks_old_price_stale(client, wl_override, monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [
        WatchlistRow(
            symbol="NVDA",
            name="NVIDIA",
            price=110.0,
            prev_close=100.0,
            change=10.0,
            change_pct=10.0,
            as_of=old,
            spark=[100.0],
        ),
    ]
    monkeypatch.setattr(watchlist_mod, "build_watchlist_rows", lambda db, tickers: rows)
    resp = client.get("/watchlist")
    assert resp.json()["tickers"][0]["stale"] is True


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "sentinel"


# ---- risk endpoints ----

def test_risk_profiles_ladder(client):
    resp = client.get("/risk/profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["profiles"]) == 10
    p1 = body["profiles"][0]
    p10 = body["profiles"][9]
    assert p1["max_position_pct"] == 5 and p10["max_position_pct"] == 20
    # Gate anchors deviate from spec §6 to stay inside the model's output range;
    # test_risk.py::test_conviction_gate_is_reachable_by_the_model explains why.
    assert p1["min_conviction"] == 20 and p10["min_conviction"] == 5
    assert p1["min_conviction"] > p10["min_conviction"]


def test_risk_profile_single(client):
    resp = client.get("/risk/profile", params={"risk_factor": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_factor"] == 5
    assert body["max_exposure_pct"] == 70


def test_risk_profile_out_of_range(client):
    assert client.get("/risk/profile", params={"risk_factor": 11}).status_code == 422


# ---- equity must come from the venue, not a possibly-dead worker ----

def _summary(monkeypatch, broker_equity, live_rows, replay_rows=(99_695.0,)):
    """Run _summarize with a stubbed broker and a chosen ledger state."""
    from types import SimpleNamespace

    from sentinel.api.routers import performance as perf
    from sentinel.config import Settings

    monkeypatch.setattr(
        perf, "_broker_equity",
        lambda: (broker_equity, "broker", datetime.now(timezone.utc))
        if broker_equity is not None else (None, "", None),
    )
    # Replay rows are present on any seeded install, so `snaps` is non-empty
    # even when the local worker has never recorded a forward cycle — which is
    # exactly the state the second machine was in.
    snaps = [
        SimpleNamespace(ts=datetime(2026, 7, 1, tzinfo=timezone.utc),
                        equity=e, source="replay")
        for e in replay_rows
    ] + [
        SimpleNamespace(ts=datetime(2026, 7, 31, 19, 57, tzinfo=timezone.utc),
                        equity=e, source="live")
        for e in live_rows
    ]
    db = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(scalar_one=lambda: 2))
    points = [
        perf.PerformancePoint(ts=s.ts, equity=float(s.equity), drawdown_pct=0.0, spy=None)
        for s in snaps
    ]
    return perf._summarize(db, Settings(), snaps, points, lambda ts: None)


def test_equity_prefers_the_broker_over_a_stale_ledger(monkeypatch):
    """The bug: a second machine sharing one Alpaca account reported $100,000
    and 0.00% while real positions were open at the venue."""
    s = _summary(monkeypatch, broker_equity=100_205.26, live_rows=[100_000.0])
    assert s.equity == 100_205.26
    assert s.equity_source == "broker"
    assert s.pnl == 205.26 and s.return_pct == 0.205


def test_equity_falls_back_to_the_ledger_when_the_broker_is_unreachable(monkeypatch):
    s = _summary(monkeypatch, broker_equity=None, live_rows=[100_058.25, 100_205.26])
    assert s.equity == 100_205.26
    assert s.equity_source == "ledger"
    assert s.as_of is not None


def test_equity_reports_baseline_when_neither_source_exists(monkeypatch):
    """Must be labelled, not silently presented as current."""
    s = _summary(monkeypatch, broker_equity=None, live_rows=[])
    assert s.equity == 100_000.0
    assert s.equity_source == "baseline"
    assert s.return_pct == 0.0


def test_pnl_and_return_stay_consistent_with_whichever_source_won(monkeypatch):
    for eq in (100_205.26, 99_500.0):
        s = _summary(monkeypatch, broker_equity=eq, live_rows=[100_000.0])
        assert s.pnl == round(eq - 100_000.0, 2)
        assert s.return_pct == round(s.pnl / 100_000.0 * 100, 3)
