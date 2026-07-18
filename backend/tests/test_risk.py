"""Risk profile, sizing, and hard-breaker tests (spec §6)."""

from __future__ import annotations

import pytest

from sentinel.risk.breakers import check_breakers, validate_order
from sentinel.risk.manager import size_position
from sentinel.risk.profile import risk_profile


def test_risk_profile_hits_spec_anchors():
    # Spec §6 anchor table must be reproduced exactly at 1 / 5 / 10.
    p1, p5, p10 = risk_profile(1), risk_profile(5), risk_profile(10)
    assert (p1.max_position_pct, p5.max_position_pct, p10.max_position_pct) == (5, 12, 20)
    assert (p1.max_exposure_pct, p5.max_exposure_pct, p10.max_exposure_pct) == (30, 70, 95)
    assert (p1.min_conviction, p5.min_conviction, p10.min_conviction) == (70, 50, 35)
    assert (p1.stop_atr_mult, p5.stop_atr_mult, p10.stop_atr_mult) == (1.5, 2.5, 3.5)
    assert (p1.max_new_positions_per_day, p5.max_new_positions_per_day,
            p10.max_new_positions_per_day) == (1, 2, 4)
    assert p1.trade_around_earnings == "never"
    assert p10.trade_around_earnings == "allowed"


def test_risk_profile_interpolates_between_anchors():
    p3 = risk_profile(3)
    # Piecewise-linear halfway between r=1 and r=5.
    assert p3.max_position_pct == pytest.approx(8.5, abs=0.01)
    assert p3.max_exposure_pct == pytest.approx(50.0, abs=0.01)


def test_risk_profile_clamps():
    assert risk_profile(0).risk_factor == 1
    assert risk_profile(99).risk_factor == 10


def test_size_position_basic():
    p = risk_profile(5)  # 12% max position, 70% exposure
    d = size_position(
        equity=100_000, price=100.0, atr=2.0, profile=p, current_exposure_value=0.0
    )
    assert d.allowed
    assert d.shares == 120  # 12% of 100k / $100
    assert d.stop_price == 95.0  # 100 - 2.5*2.0
    assert d.take_profit == 110.0  # 100 + 2*(2.5*2.0)


def test_size_position_respects_exposure_headroom():
    p = risk_profile(5)
    d = size_position(
        equity=100_000, price=100.0, atr=2.0, profile=p,
        current_exposure_value=69_500,  # only $500 headroom under 70% cap
    )
    assert d.shares == 5


def test_size_position_blocked_when_exposure_full():
    p = risk_profile(5)
    d = size_position(
        equity=100_000, price=100.0, atr=2.0, profile=p, current_exposure_value=70_000
    )
    assert d.blocked
    assert "exposure" in d.reason


def test_daily_loss_breaker_trips_at_3pct():
    r = check_breakers(
        equity=96_900, day_start_equity=100_000, high_water_mark=100_000
    )
    assert r.daily_loss_tripped
    assert "flatten_all" in r.actions and "halt_for_day" in r.actions


def test_drawdown_breaker_trips_at_12pct():
    r = check_breakers(
        equity=87_000, day_start_equity=88_000, high_water_mark=100_000
    )
    assert r.drawdown_tripped
    assert "lock_to_dry_run" in r.actions


def test_no_breaker_when_healthy():
    r = check_breakers(
        equity=101_000, day_start_equity=100_000, high_water_mark=100_000
    )
    assert not r.any_tripped


def test_validate_order_duplicate_guard():
    chk = validate_order(
        symbol="NVDA", side="buy", qty=10, limit_price=100.0, last_price=100.0,
        equity=100_000, pending_keys={("NVDA", "buy")},
    )
    assert not chk.ok and "duplicate" in chk.reason


def test_validate_order_price_collar():
    chk = validate_order(
        symbol="NVDA", side="buy", qty=10, limit_price=120.0, last_price=100.0,
        equity=100_000, pending_keys=set(),
    )
    assert not chk.ok and "off last" in chk.reason


def test_validate_order_ok():
    chk = validate_order(
        symbol="NVDA", side="buy", qty=10, limit_price=100.5, last_price=100.0,
        equity=100_000, pending_keys=set(),
    )
    assert chk.ok
