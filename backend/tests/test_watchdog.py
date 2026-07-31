"""Worker liveness: stage budgets and the hard watchdog.

Regression cover for 2026-07-31, when an Alpaca call with no socket timeout
wedged the loop for 66 minutes while the container still reported healthy.
"""

from __future__ import annotations

import time

import pytest

from sentinel.worker.watchdog import HardWatchdog, StageTimeout, time_limit


def test_time_limit_interrupts_a_blocking_call():
    """The whole point: a sleep with no timeout must not run to completion."""
    started = time.monotonic()
    with pytest.raises(StageTimeout, match="stuck stage"):
        with time_limit(0.2, "stuck stage"):
            time.sleep(5)
    assert time.monotonic() - started < 2  # interrupted, not waited out


def test_time_limit_lets_fast_work_finish():
    with time_limit(5.0, "quick stage"):
        result = 2 + 2
    assert result == 4


def test_time_limit_is_disarmed_afterwards():
    """A leftover timer would fire during an unrelated later stage."""
    with time_limit(0.2, "first"):
        pass
    time.sleep(0.4)  # would have fired by now if still armed
    with time_limit(5.0, "second"):
        pass


def test_time_limit_disarms_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with time_limit(5.0, "boom"):
            raise ValueError("from the body")
    time.sleep(0.1)
    with time_limit(5.0, "after"):
        pass


def test_zero_budget_means_no_limit():
    with time_limit(0, "unbounded"):
        time.sleep(0.1)  # must not raise


def test_watchdog_tracks_progress():
    w = HardWatchdog(limit_seconds=10.0)
    assert w._age() < 1.0
    time.sleep(0.2)
    w.beat()
    assert w._age() < 0.2


def test_watchdog_exits_when_progress_stops(monkeypatch):
    """The backstop for a hang SIGALRM cannot break."""
    exits = []
    monkeypatch.setattr(
        "sentinel.worker.watchdog.os._exit", lambda code: exits.append(code)
    )
    w = HardWatchdog(limit_seconds=0.1, check_every=0.05)
    w.start()
    time.sleep(0.5)  # never beat()
    w.stop()
    assert exits and exits[0] == 1


def test_watchdog_stays_quiet_while_the_loop_beats(monkeypatch):
    exits = []
    monkeypatch.setattr(
        "sentinel.worker.watchdog.os._exit", lambda code: exits.append(code)
    )
    w = HardWatchdog(limit_seconds=0.3, check_every=0.05)
    w.start()
    for _ in range(6):
        time.sleep(0.1)
        w.beat()
    w.stop()
    assert exits == []


def test_disabled_watchdog_never_starts():
    w = HardWatchdog(limit_seconds=0)
    w.start()
    assert w._thread is None
