"""Go-live gate + mode-switch integration (needs a real Postgres via
SENTINEL_TEST_DATABASE_URL; skipped otherwise)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sentinel.config import Settings
from sentinel.golive.gate import evaluate_gate
from sentinel.golive.mode import unlock_live
from sentinel.golive.review import record_review, review_count, sample_unreviewed
from sentinel.models import BacktestRun, Base, Decision, EquitySnapshot
from sentinel.system_state import ensure_dry_run_started, record_breaker_event


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


def _seed_equity(session, days, start_equity=100_000, growth=0.001):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    eq = start_equity
    for i in range(days):
        eq *= 1 + growth
        session.add(EquitySnapshot(
            ts=base + timedelta(days=i), equity=eq, cash=eq, exposure_pct=0.0, mode="DRY_RUN"
        ))
    session.commit()


def test_gate_fails_early_then_criteria_move(db):
    s = Settings(golive_min_trading_days=60, golive_min_reviews=20)
    ensure_dry_run_started(db, datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.commit()

    gate = evaluate_gate(db, s, ["NVDA"])
    assert not gate.passed
    keys = {c.key for c in gate.criteria}
    assert keys == {"trading_days", "excess_return", "drawdown", "breakers", "reviews"}
    assert not next(c for c in gate.criteria if c.key == "trading_days").passed

    # Add 60 days of rising equity -> trading_days criterion passes.
    _seed_equity(db, 60)
    gate = evaluate_gate(db, s, ["NVDA"])
    assert next(c for c in gate.criteria if c.key == "trading_days").passed


def test_reviews_criterion(db):
    s = Settings(golive_min_reviews=3)
    for i in range(5):
        db.add(Decision(
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc), symbol="NVDA", action="SKIP",
            signal="PASS", conviction=0.0, confidence=0.0, risk_factor=5, mode="DRY_RUN",
            reason="x", drivers=[],
        ))
    db.commit()
    assert review_count(db) == 0
    sample = sample_unreviewed(db, n=3)
    assert len(sample) == 3
    for d in sample:
        record_review(db, d.id, ok=True)
    db.commit()
    assert review_count(db) == 3
    gate = evaluate_gate(db, s, ["NVDA"])
    assert next(c for c in gate.criteria if c.key == "reviews").passed


def test_unlock_live_blocked_until_gate_and_confirmation(db):
    s = Settings()
    ensure_dry_run_started(db, datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.commit()
    # Wrong confirmation phrase.
    r = unlock_live(db, s, ["NVDA"], "yes please")
    assert not r.ok and "confirmation" in r.reason
    # Right phrase but gate not passed.
    r = unlock_live(db, s, ["NVDA"], "GO LIVE")
    assert not r.ok and "gate" in r.reason
    assert r.mode == "DRY_RUN"


def test_breaker_cooloff_blocks_unlock(db):
    s = Settings(live_cooloff_hours=24)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ensure_dry_run_started(db, datetime(2026, 1, 1, tzinfo=timezone.utc))
    # A breaker just fired -> cool-off blocks unlock (checked before the gate).
    record_breaker_event(db, ts=now, kind="daily_loss", detail="test")
    db.commit()
    r = unlock_live(db, s, ["NVDA"], "GO LIVE", now=now + timedelta(hours=1))
    assert not r.ok and "cool-off" in r.reason
