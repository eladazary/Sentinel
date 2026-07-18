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
    assert names == {"database": True, "redis": True}


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
