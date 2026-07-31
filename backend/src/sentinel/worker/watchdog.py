"""Keep the worker loop from hanging forever.

A blocking call with no timeout stops the desk silently: the container still
reports healthy, Docker sees a live process, and the only symptom is that
nothing gets written. That happened for 66 minutes on 2026-07-31 — an Alpaca
call with no socket timeout (alpaca-py drives ``requests`` without one), which no
amount of per-library configuration would have caught generically.

Two layers, because they fail differently:

* ``time_limit`` uses SIGALRM, which interrupts a blocking syscall and raises in
  the main thread. A stuck stage becomes a logged, recovered cycle. This is the
  primary mechanism and it is *recoverable*.
* ``HardWatchdog`` is the backstop for a hang SIGALRM cannot break (a C-level
  lock, or a stage that swallows the exception). It exits the process so
  ``restart: unless-stopped`` gets a chance to do its job. Blunt on purpose:
  a restarted worker beats a wedged one.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from sentinel.logging_config import get_logger

log = get_logger("sentinel.worker.watchdog")


class StageTimeout(TimeoutError):
    """A loop stage outran its budget and was interrupted."""


@contextmanager
def time_limit(seconds: float, label: str) -> Iterator[None]:
    """Interrupt the wrapped block if it runs longer than ``seconds``.

    Relies on SIGALRM, so it only works in the main thread on a POSIX host —
    both true for the worker. PEP 475 would normally retry a syscall interrupted
    by a signal, but the handler raises, so the exception propagates instead.
    """
    if seconds <= 0:
        yield
        return

    def _handler(_signum, _frame):
        raise StageTimeout(f"{label} exceeded its {seconds:.0f}s budget")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class HardWatchdog:
    """Exit the process if the loop stops making progress.

    ``beat()`` is called once per completed iteration. A daemon thread checks the
    gap and, past ``limit_seconds``, logs and exits. Nothing here tries to be
    clever about *why* progress stopped — that's what the logs are for.
    """

    def __init__(self, limit_seconds: float, check_every: float = 30.0):
        self._limit = limit_seconds
        self._check_every = check_every
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def beat(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def _age(self) -> float:
        with self._lock:
            return time.monotonic() - self._last

    def start(self) -> None:
        if self._limit <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="sentinel-watchdog", daemon=True
        )
        self._thread.start()
        log.info("watchdog armed: exit if no cycle completes for %.0fs", self._limit)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._check_every):
            age = self._age()
            if age > self._limit:
                log.critical(
                    "WORKER WEDGED — no cycle completed in %.0fs (limit %.0fs). "
                    "Exiting so the container restarts; check the last log lines "
                    "for the stage that hung.",
                    age, self._limit,
                )
                # os._exit: a wedged thread may be holding the GIL in C code, so
                # sys.exit / raise would not reliably tear the process down.
                os._exit(1)
