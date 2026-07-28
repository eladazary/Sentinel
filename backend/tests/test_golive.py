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
from sentinel.models import BacktestRun, Base, DailyBar, Decision, EquitySnapshot
from sentinel.system_state import (
    ensure_dry_run_started,
    get_state,
    record_breaker_event,
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


def _seed_equity(session, days, start_equity=100_000, growth=0.001, source="live"):
    """Seed `days` *trading* days of equity — weekdays only, mid-session.

    A real dry run only accumulates weekdays, and trading_days_count now buckets
    in market time, so 15:00 UTC (11:00 ET) keeps each row on its own ET day.
    """
    ts = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
    eq = start_equity
    seeded = 0
    while seeded < days:
        if ts.weekday() < 5:  # Mon-Fri
            eq *= 1 + growth
            session.add(EquitySnapshot(
                ts=ts, equity=eq, cash=eq, exposure_pct=0.0,
                mode="DRY_RUN", source=source,
            ))
            seeded += 1
        ts += timedelta(days=1)
    session.commit()


def test_paper_return_ignores_the_replay_seam(db):
    """Two flat series at different levels must read as 0%, not as the step."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):  # replay: flat at 99,695
        db.add(EquitySnapshot(
            ts=base + timedelta(days=i), equity=99_695, cash=99_695,
            exposure_pct=0.0, mode="DRY_RUN", source="replay",
        ))
    for i in range(5):  # live: flat at 100,000
        db.add(EquitySnapshot(
            ts=base + timedelta(days=10 + i), equity=100_000, cash=100_000,
            exposure_pct=0.0, mode="DRY_RUN", source="live",
        ))
    for i in range(15):  # a flat basket, so excess isolates the paper return
        db.add(DailyBar(
            symbol="NVDA", ts=base + timedelta(days=i), open=100, high=100,
            low=100, close=100, volume=1_000,
        ))
    # get_state() starts the clock at row-creation time, so set it explicitly
    # rather than via ensure_dry_run_started, which won't override it.
    get_state(db).dry_run_started_at = base
    db.commit()

    gate = evaluate_gate(db, Settings(), ["NVDA"])
    excess = next(c for c in gate.criteria if c.key == "excess_return")
    # Neither series moves internally, so the paper return is flat. Spanning the
    # boundary would have reported 100000/99695 - 1 = +0.31%.
    assert "paper 0.0%" in excess.detail


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


# ---- trading-day counting (the go-live gate's most load-bearing number) ----

def _snap(session, ts, source="live"):
    session.add(EquitySnapshot(
        ts=ts, equity=100_000, cash=100_000, exposure_pct=0.0,
        mode="DRY_RUN", source=source,
    ))


def test_weekends_do_not_count_as_trading_days(db):
    from sentinel.system_state import trading_days_count

    # Mon-Fri 2026-07-20..24, then Sat 25 and Sun 26 (all 15:00 UTC = 11:00 ET).
    for day in range(20, 27):
        _snap(db, datetime(2026, 7, day, 15, 0, tzinfo=timezone.utc))
    db.commit()
    # 7 calendar days present, but only the 5 weekdays are trading days.
    assert trading_days_count(db) == 5


def test_day_is_bucketed_in_market_time_not_utc(db):
    """00:30 UTC Saturday is 20:30 Friday in New York — a real trading day."""
    from sentinel.system_state import trading_days_count

    _snap(db, datetime(2026, 7, 25, 0, 30, tzinfo=timezone.utc))  # Fri 20:30 ET
    db.commit()
    assert trading_days_count(db) == 1

    # Whereas mid-day Saturday ET is not, and must not add a second day.
    _snap(db, datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc))  # Sat 12:00 ET
    db.commit()
    assert trading_days_count(db) == 1


def test_multiple_snapshots_in_one_session_count_once(db):
    from sentinel.system_state import trading_days_count

    for minute in (0, 15, 30, 45):
        _snap(db, datetime(2026, 7, 22, 15, minute, tzinfo=timezone.utc))  # Wed
    db.commit()
    assert trading_days_count(db) == 1


# ---- decision log filtering (the log buries real trades under skips) ----

def _decision(session, symbol, action, ts, reason="x", order_id=None):
    session.add(Decision(
        ts=ts, symbol=symbol, action=action, signal="BUY", conviction=10.0,
        confidence=0.5, risk_factor=7, mode="DRY_RUN", reason=reason,
        drivers=[], broker_order_id=order_id,
    ))


def test_decision_filters(db):
    from sentinel.execution.decision_log import (
        decision_action_counts,
        recent_decisions,
    )

    base = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
    for i in range(20):  # the noise
        _decision(db, "NVDA", "SKIP", base + timedelta(seconds=i))
    _decision(db, "AAPL", "OPEN", base + timedelta(seconds=30), order_id="o1")
    _decision(db, "GOOGL", "OPEN", base + timedelta(seconds=31), order_id="o2")
    _decision(db, "AAPL", "EXIT", base + timedelta(seconds=40), order_id="o3")
    db.commit()

    assert len(recent_decisions(db, limit=100)) == 23

    # exclude_skips is the common case: what actually happened.
    trades = recent_decisions(db, limit=100, exclude_skips=True)
    assert {d.action for d in trades} == {"OPEN", "EXIT"}
    assert len(trades) == 3

    # Explicit action list.
    opens = recent_decisions(db, limit=100, actions=["open"])  # case-insensitive
    assert len(opens) == 2 and all(d.action == "OPEN" for d in opens)

    # Per-symbol, and it composes with the skip filter.
    aapl = recent_decisions(db, limit=100, symbol="aapl", exclude_skips=True)
    assert {d.action for d in aapl} == {"OPEN", "EXIT"}
    assert all(d.symbol == "AAPL" for d in aapl)

    # Newest first.
    assert trades[0].action == "EXIT"

    assert decision_action_counts(db) == {"SKIP": 20, "OPEN": 2, "EXIT": 1}
