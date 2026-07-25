"""Runtime-editable watchlist (needs Postgres via SENTINEL_TEST_DATABASE_URL)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sentinel.config import Settings
from sentinel.models import Base
from sentinel.universe import (
    MAX_TICKERS,
    UniverseError,
    add_ticker,
    load_universe,
    mark_backfilled,
    pending_backfill,
    remove_ticker,
)


@pytest.fixture
def db(test_database_url):
    engine = create_engine(test_database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_seeds_from_yaml_once_then_is_authoritative(db):
    s = Settings()
    seeded = load_universe(db, s)
    assert len(seeded.tickers) >= 1

    add_ticker(db, "GOOGL", "Alphabet", "XLC")
    db.commit()

    # A second load must not re-seed from the file and drop the new name.
    again = load_universe(db, s)
    assert "GOOGL" in again.symbols
    assert len(again.tickers) == len(seeded.tickers) + 1


def test_add_normalizes_and_rejects_duplicates(db):
    s = Settings()
    load_universe(db, s)
    add_ticker(db, "  googl ", "Alphabet", "XLC")
    db.commit()
    assert "GOOGL" in load_universe(db, s).symbols

    with pytest.raises(UniverseError, match="already in the watchlist"):
        add_ticker(db, "googl", "Alphabet", "XLC")


def test_add_rejects_blank_symbol(db):
    load_universe(db, Settings())
    with pytest.raises(UniverseError, match="symbol is required"):
        add_ticker(db, "   ", "Nothing")


def test_cap_is_enforced(db):
    s = Settings()
    existing = len(load_universe(db, s).tickers)
    for i in range(MAX_TICKERS - existing):
        add_ticker(db, f"TST{i}", f"Test {i}", "XLK")
    db.commit()
    with pytest.raises(UniverseError, match="full"):
        add_ticker(db, "ONEMORE", "One more", "XLK")


def test_remove_and_the_last_one_is_protected(db):
    s = Settings()
    symbols = load_universe(db, s).symbols
    assert remove_ticker(db, symbols[0].lower()) is True  # case-insensitive
    db.commit()
    assert symbols[0] not in load_universe(db, s).symbols

    assert remove_ticker(db, "NOTREAL") is False

    for sym in load_universe(db, s).symbols[1:]:
        remove_ticker(db, sym)
    db.commit()
    with pytest.raises(UniverseError, match="can't be empty"):
        remove_ticker(db, load_universe(db, s).symbols[0])


def test_new_tickers_are_pending_backfill_but_seeded_ones_are_not(db):
    s = Settings()
    load_universe(db, s)
    assert pending_backfill(db) == []  # seeded names come from the boot sweep

    add_ticker(db, "GOOGL", "Alphabet", "XLC")
    db.commit()
    assert [t.symbol for t in pending_backfill(db)] == ["GOOGL"]

    mark_backfilled(db, "GOOGL")
    db.commit()
    assert pending_backfill(db) == []
