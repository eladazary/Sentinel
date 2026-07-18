"""Integration tests for the data-access layer against a real Postgres.

Skipped unless ``SENTINEL_TEST_DATABASE_URL`` points at a Postgres/TimescaleDB
instance. Exercises the ON CONFLICT upserts and the watchlist assembly that the
offline tests can't cover.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sentinel import repositories as repo
from sentinel.ingestion.alpaca import normalize_bar
from sentinel.models import Base
from types import SimpleNamespace


@pytest.fixture
def db_session(test_database_url):
    engine = create_engine(test_database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _bar(symbol, day, close):
    return normalize_bar(
        symbol,
        SimpleNamespace(
            timestamp=datetime(2024, 1, day, tzinfo=timezone.utc),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000 + day,
            trade_count=10,
            vwap=close,
        ),
    )


def test_upsert_daily_bars_is_idempotent(db_session):
    rows = [_bar("NVDA", 2, 100.0), _bar("NVDA", 3, 101.0)]
    repo.upsert_daily_bars(db_session, rows)
    db_session.commit()
    assert repo.count_daily_bars(db_session, "NVDA") == 2

    # Re-upsert with a changed close -> still 2 rows, value updated.
    repo.upsert_daily_bars(db_session, [_bar("NVDA", 3, 999.0)])
    db_session.commit()
    assert repo.count_daily_bars(db_session, "NVDA") == 2
    assert repo.get_last_close(db_session, "NVDA") == 999.0


def test_recent_closes_order(db_session):
    repo.upsert_daily_bars(
        db_session, [_bar("MSFT", d, 100.0 + d) for d in (2, 3, 4)]
    )
    db_session.commit()
    closes = repo.get_recent_closes(db_session, "MSFT", limit=2)
    # oldest-first, last two days
    assert closes[0][1] == 103.0 and closes[1][1] == 104.0


def test_latest_price_upsert_and_watchlist_rows(db_session):
    repo.upsert_daily_bars(db_session, [_bar("NVDA", 2, 100.0)])
    ts = datetime(2024, 1, 3, tzinfo=timezone.utc)
    repo.upsert_latest_price(db_session, "NVDA", 110.0, ts, updated_at=ts)
    db_session.commit()

    rows = repo.build_watchlist_rows(db_session, [("NVDA", "NVIDIA")])
    assert len(rows) == 1
    row = rows[0]
    assert row.price == 110.0
    assert row.prev_close == 100.0
    assert row.change == 10.0
    assert round(row.change_pct, 4) == 10.0
    assert row.spark == [100.0]
