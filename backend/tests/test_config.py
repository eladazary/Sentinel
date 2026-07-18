"""Config & watchlist loading/validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel.config import Settings, Ticker, Watchlist, load_watchlist


def test_ticker_symbol_normalized():
    t = Ticker(symbol="  nvda ", name="  NVIDIA ")
    assert t.symbol == "NVDA"
    assert t.name == "NVIDIA"


def test_watchlist_symbols_and_name_lookup(sample_watchlist):
    assert sample_watchlist.symbols == ["NVDA", "MSFT"]
    assert sample_watchlist.name_for("MSFT") == "Microsoft"
    assert sample_watchlist.name_for("ZZZZ") is None


def test_watchlist_rejects_duplicates():
    with pytest.raises(ValidationError):
        Watchlist(
            tickers=[Ticker(symbol="AAPL", name="A"), Ticker(symbol="AAPL", name="B")]
        )


def test_watchlist_rejects_empty():
    with pytest.raises(ValidationError):
        Watchlist(tickers=[])


def test_watchlist_rejects_over_ten():
    tickers = [Ticker(symbol=f"T{i}", name=str(i)) for i in range(11)]
    with pytest.raises(ValidationError):
        Watchlist(tickers=tickers)


def test_load_watchlist_from_file(tmp_path):
    p = tmp_path / "wl.yaml"
    p.write_text(
        "tickers:\n  - symbol: aapl\n    name: Apple\n  - symbol: msft\n    name: Microsoft\n"
    )
    wl = load_watchlist(p)
    assert wl.symbols == ["AAPL", "MSFT"]


def test_load_watchlist_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_watchlist(tmp_path / "nope.yaml")


def test_bundled_watchlist_loads():
    # The repo's default config/watchlist.yaml must be valid.
    wl = load_watchlist("config/watchlist.yaml")
    assert 1 <= len(wl.tickers) <= 10


def test_settings_mode_validation():
    assert Settings(mode="live").mode == "LIVE"
    with pytest.raises(ValidationError):
        Settings(mode="paper")


def test_settings_feed_validation():
    assert Settings(alpaca_data_feed="SIP").alpaca_data_feed == "sip"
    with pytest.raises(ValidationError):
        Settings(alpaca_data_feed="nasdaq")


def test_has_alpaca_credentials():
    assert not Settings(alpaca_api_key=None, alpaca_secret_key=None).has_alpaca_credentials
    assert Settings(
        alpaca_api_key="k", alpaca_secret_key="s"
    ).has_alpaca_credentials
