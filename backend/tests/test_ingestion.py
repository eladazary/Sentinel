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


# ---- quote source is separate from backfill ----

def _stub_working_alpaca(monkeypatch):
    """A quote feed that answers. Stubbed so the test never hits the network —
    with real credentials absent, a live probe would 401 and degrade."""
    from datetime import datetime, timezone

    from sentinel.ingestion import prices as prices_mod

    class _Working:
        def get_latest_prices(self, symbols):
            return {s: (1.0, datetime.now(timezone.utc)) for s in symbols}

    monkeypatch.setattr(prices_mod, "_alpaca_market_data", lambda s: _Working())


def test_auto_quote_source_prefers_alpaca_when_credentials_work(monkeypatch):
    from sentinel.config import Settings
    from sentinel.ingestion.prices import make_quote_source

    _stub_working_alpaca(monkeypatch)
    s = Settings(alpaca_api_key="PKTEST", alpaca_secret_key="secret")
    _, name = make_quote_source(s)
    assert name == "alpaca"


def test_auto_quote_source_falls_back_to_yfinance_without_credentials():
    from sentinel.config import Settings
    from sentinel.ingestion.prices import make_quote_source

    s = Settings(alpaca_api_key=None, alpaca_secret_key=None)
    _, name = make_quote_source(s)
    assert name == "yfinance"


def test_quote_source_is_independent_of_backfill_source(monkeypatch):
    """Backfill on yfinance must not drag quotes onto daily closes with it."""
    from sentinel.config import Settings
    from sentinel.ingestion.prices import make_quote_source

    _stub_working_alpaca(monkeypatch)
    s = Settings(
        backfill_source="yfinance",
        alpaca_api_key="PKTEST",
        alpaca_secret_key="secret",
    )
    assert s.backfill_source == "yfinance"
    _, name = make_quote_source(s)
    assert name == "alpaca"


def test_explicit_alpaca_quote_source_requires_credentials():
    from sentinel.config import Settings
    from sentinel.ingestion.prices import make_quote_source

    s = Settings(quote_source="alpaca", alpaca_api_key=None, alpaca_secret_key=None)
    with pytest.raises(ValueError, match="credentials are unset"):
        make_quote_source(s)


def test_ingest_records_the_provider_that_served_the_quote(monkeypatch):
    """The source column used to say "alpaca" even for yfinance closes."""
    from contextlib import contextmanager
    from datetime import datetime, timezone

    from sentinel.ingestion import prices as prices_mod

    captured = []

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(
        prices_mod.repo, "upsert_latest_price",
        lambda session, symbol, price, ts, updated_at, source="alpaca": captured.append(source),
    )
    md = SimpleNamespace(
        get_latest_prices=lambda syms: {
            s: (1.0, datetime.now(timezone.utc)) for s in syms
        }
    )
    prices_mod.ingest_latest_prices(
        md, ["AAA"], session_factory=fake_session, source="yfinance"
    )
    assert captured == ["yfinance"]


# ---- quote feed probing: rejected credentials must not fail silently ----

@pytest.fixture(autouse=True)
def _clean_quote_probe():
    from sentinel.ingestion.prices import reset_quote_probe
    reset_quote_probe()
    yield
    reset_quote_probe()


def test_rejected_alpaca_quotes_degrade_to_yfinance(monkeypatch):
    """Present-but-rejected keys used to 401 every batched call and write nothing,
    so every symbol showed up as "no quote" with the cause nowhere in sight."""
    from sentinel.config import Settings
    from sentinel.ingestion import prices as prices_mod

    class _Rejecting:
        def get_latest_prices(self, symbols):
            raise RuntimeError("401 Client Error: Unauthorized")

    monkeypatch.setattr(prices_mod, "_alpaca_market_data", lambda s: _Rejecting())
    s = Settings(alpaca_api_key="PKTEST", alpaca_secret_key="secret")
    _, name = prices_mod.make_quote_source(s)
    assert name == "yfinance"


def test_working_alpaca_quotes_are_used(monkeypatch):
    from datetime import datetime, timezone

    from sentinel.config import Settings
    from sentinel.ingestion import prices as prices_mod

    class _Working:
        def get_latest_prices(self, symbols):
            return {s: (1.0, datetime.now(timezone.utc)) for s in symbols}

    monkeypatch.setattr(prices_mod, "_alpaca_market_data", lambda s: _Working())
    s = Settings(alpaca_api_key="PKTEST", alpaca_secret_key="secret")
    _, name = prices_mod.make_quote_source(s)
    assert name == "alpaca"


def test_probe_is_cached_so_it_is_not_run_per_cycle(monkeypatch):
    from datetime import datetime, timezone

    from sentinel.config import Settings
    from sentinel.ingestion import prices as prices_mod

    calls = []

    class _Counting:
        def get_latest_prices(self, symbols):
            calls.append(1)
            return {s: (1.0, datetime.now(timezone.utc)) for s in symbols}

    monkeypatch.setattr(prices_mod, "_alpaca_market_data", lambda s: _Counting())
    s = Settings(alpaca_api_key="PKTEST", alpaca_secret_key="secret")
    prices_mod.make_quote_source(s)
    prices_mod.make_quote_source(s)
    assert len(calls) == 1


def test_delayed_feed_cannot_price_an_entry():
    """The freshness guard measures *our fetch time*, so a yfinance daily close
    pulled a second ago looks fresh while being a day stale."""
    from sentinel.execution.loop import Quote

    live = Quote(price=100.0, age_seconds=5.0, source="alpaca")
    assert live.usable_for_entry(300.0) is None

    delayed = Quote(price=100.0, age_seconds=5.0, source="yfinance")
    assert "delayed closes" in delayed.usable_for_entry(300.0)

    stale = Quote(price=100.0, age_seconds=9999.0, source="alpaca")
    assert "old" in stale.usable_for_entry(300.0)

    unknown = Quote(price=100.0, age_seconds=1.0)
    assert unknown.usable_for_entry(300.0) is not None


# ---- one bad symbol must not zero every quote ----

def _md(supports_index, bad=()):
    from datetime import datetime, timezone

    class _MD:
        supports_index_symbols = supports_index

        def get_latest_prices(self, symbols):
            offenders = [s for s in symbols if s in bad]
            if offenders:  # Alpaca 400s the whole batch, not just the offender
                raise RuntimeError(f"400 Bad Request for {offenders}")
            return {s: (1.0, datetime.now(timezone.utc)) for s in symbols}

    return _MD()


def test_index_symbols_are_dropped_for_feeds_that_reject_them():
    """^VIX 400'd Alpaca's whole batch, silently zeroing every watchlist quote."""
    from sentinel.ingestion.prices import _quote_eligible

    syms = ["NVDA", "SPY", "^VIX"]
    assert _quote_eligible(_md(False), syms) == ["NVDA", "SPY"]
    assert _quote_eligible(_md(True), syms) == syms  # yfinance serves indices


def test_a_rejected_symbol_no_longer_costs_the_others_their_quotes(monkeypatch):
    from contextlib import contextmanager

    from sentinel.ingestion import prices as prices_mod

    saved = {}

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(
        prices_mod.repo, "upsert_latest_price",
        lambda session, symbol, price, ts, updated_at, source="alpaca": saved.setdefault(symbol, price),
    )
    n = prices_mod.ingest_latest_prices(
        _md(True, bad=("BADSYM",)),
        ["NVDA", "BADSYM", "AAPL"],
        session_factory=fake_session,
        source="alpaca",
    )
    # The good ones still land; only the offender is lost.
    assert n == 2
    assert set(saved) == {"NVDA", "AAPL"}


def test_ingest_returns_zero_when_nothing_is_eligible(monkeypatch):
    from contextlib import contextmanager

    from sentinel.ingestion import prices as prices_mod

    @contextmanager
    def fake_session():
        yield object()

    assert prices_mod.ingest_latest_prices(
        _md(False), ["^VIX"], session_factory=fake_session
    ) == 0
