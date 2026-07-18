"""Ingestion logic: bar normalization, backfill orchestration, latest polling.

These use a fake MarketData provider and monkeypatched repository functions, so
no network or database is required.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sentinel.config import Ticker, Watchlist
from sentinel.ingestion import prices
from sentinel.ingestion.alpaca import normalize_bar


def make_bar(**kw):
    base = dict(
        timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
        open=10.0,
        high=11.0,
        low=9.5,
        close=10.5,
        volume=1000,
        trade_count=42,
        vwap=10.3,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_normalize_bar_full():
    row = normalize_bar("NVDA", make_bar())
    assert row["symbol"] == "NVDA"
    assert row["open"] == 10.0 and row["close"] == 10.5
    assert row["volume"] == 1000 and isinstance(row["volume"], int)
    assert row["trade_count"] == 42
    assert row["vwap"] == 10.3


def test_normalize_bar_missing_optional_fields():
    bar = make_bar(trade_count=None, vwap=None)
    row = normalize_bar("MSFT", bar)
    assert row["trade_count"] is None
    assert row["vwap"] is None


def test_backfill_window_span():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    start, end = prices.backfill_window(now, 5)
    assert end == now
    assert (end - start).days == 365 * 5


def test_iter_batches():
    assert list(prices.iter_batches([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(prices.iter_batches([], 2)) == []


class FakeMarketData:
    def __init__(self):
        self.daily_calls = []
        self.latest_calls = []

    def get_daily_bars(self, symbols, start, end):
        self.daily_calls.append((tuple(symbols), start, end))
        return {s: [normalize_bar(s, make_bar())] for s in symbols}

    def get_latest_prices(self, symbols):
        self.latest_calls.append(tuple(symbols))
        ts = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)
        return {s: (100.0 + i, ts) for i, s in enumerate(symbols)}


@contextmanager
def _fake_session():
    # The session object is never touched directly because repo functions are
    # monkeypatched; a sentinel object is enough.
    yield SimpleNamespace(name="fake-session")


@pytest.fixture
def wl():
    return Watchlist(
        tickers=[Ticker(symbol="NVDA", name="NVIDIA"), Ticker(symbol="MSFT", name="MS")]
    )


def test_backfill_writes_when_empty(monkeypatch, wl):
    written = []
    monkeypatch.setattr(prices.repo, "count_daily_bars", lambda s, sym: 0)
    monkeypatch.setattr(
        prices.repo, "upsert_daily_bars", lambda s, rows: written.append(rows) or len(rows)
    )
    md = FakeMarketData()
    result = prices.backfill_prices(md, wl, years=5, session_factory=_fake_session)
    assert result == {"NVDA": 1, "MSFT": 1}
    assert len(md.daily_calls) == 2  # one fetch per symbol
    assert len(written) == 2


def test_backfill_skips_when_data_exists(monkeypatch, wl):
    monkeypatch.setattr(prices.repo, "count_daily_bars", lambda s, sym: 500)
    called = []
    monkeypatch.setattr(
        prices.repo, "upsert_daily_bars", lambda s, rows: called.append(rows)
    )
    md = FakeMarketData()
    result = prices.backfill_prices(md, wl, years=5, session_factory=_fake_session)
    assert result == {"NVDA": 0, "MSFT": 0}
    assert md.daily_calls == []  # nothing fetched
    assert called == []


def test_backfill_force_refetches(monkeypatch, wl):
    monkeypatch.setattr(prices.repo, "count_daily_bars", lambda s, sym: 500)
    monkeypatch.setattr(prices.repo, "upsert_daily_bars", lambda s, rows: len(rows))
    md = FakeMarketData()
    result = prices.backfill_prices(
        md, wl, years=5, session_factory=_fake_session, force=True
    )
    assert result == {"NVDA": 1, "MSFT": 1}
    assert len(md.daily_calls) == 2


def test_ingest_latest_prices(monkeypatch, wl):
    upserts = []
    monkeypatch.setattr(
        prices.repo,
        "upsert_latest_price",
        lambda s, symbol, price, ts, updated_at, source="alpaca": upserts.append(
            (symbol, price)
        ),
    )
    md = FakeMarketData()
    n = prices.ingest_latest_prices(
        md, wl.symbols, session_factory=_fake_session
    )
    assert n == 2
    assert md.latest_calls == [("NVDA", "MSFT")]
    assert {u[0] for u in upserts} == {"NVDA", "MSFT"}


def test_ingest_latest_prices_empty(monkeypatch):
    md = FakeMarketData()
    assert prices.ingest_latest_prices(md, [], session_factory=_fake_session) == 0
    assert md.latest_calls == []
