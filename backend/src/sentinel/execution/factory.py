"""Choose a broker: Alpaca (paper/live) when the credentials work, else the sim.

Credentials that are *present but rejected* are the case worth care. In DRY_RUN
we degrade to the in-memory sim so the paper loop and the dashboard keep running
on a bad key. In LIVE we refuse outright — handing a simulation to something the
operator believes is trading real money is the worst failure available to us.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sentinel.config import Settings
from sentinel.execution.broker import Broker
from sentinel.logging_config import get_logger

log = get_logger(__name__)

# Probing costs an Alpaca round-trip and /account is polled by the dashboard,
# so the verdict is remembered briefly rather than re-checked per request.
_PROBE_TTL_SECONDS = 60.0

_probe_cache: tuple[float, str | None] | None = None
_sim_broker: Broker | None = None


class BrokerUnavailable(RuntimeError):
    """Raised when LIVE mode cannot get a broker it is allowed to trust."""


@dataclass(frozen=True)
class BrokerStatus:
    """Which broker the caller actually got, and why."""

    broker: str  # "alpaca" | "sim"
    degraded: bool  # credentials were supplied but unusable
    detail: str | None = None


def reset_broker_cache() -> None:
    """Forget the cached credential verdict and the sim (tests, config changes)."""
    global _probe_cache, _sim_broker
    _probe_cache = None
    _sim_broker = None


def make_broker(settings: Settings) -> Broker:
    """Back-compat entry point for callers that don't care how they got here."""
    broker, _ = make_broker_with_status(settings)
    return broker


def make_broker_with_status(settings: Settings) -> tuple[Broker, BrokerStatus]:
    live = settings.mode == "LIVE"

    if not settings.has_alpaca_credentials:
        if live:
            raise BrokerUnavailable(
                "LIVE mode requires Alpaca credentials — refusing to simulate"
            )
        log.warning("no Alpaca credentials — using in-memory SimBroker")
        return _sim(settings), BrokerStatus(
            broker="sim", degraded=False, detail="no Alpaca credentials configured"
        )

    from sentinel.execution.alpaca_broker import AlpacaBroker

    paper = settings.alpaca_paper or not live
    broker: Broker | None = None
    try:
        broker = AlpacaBroker(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=paper
        )
        err = _probe(broker)
    except Exception as exc:  # noqa: BLE001 - construction can fail on bad input
        err = describe_broker_error(exc)

    if err is None and broker is not None:
        log.info("using Alpaca broker (paper=%s)", paper)
        return broker, BrokerStatus(broker="alpaca", degraded=False)

    if live:
        raise BrokerUnavailable(err or "Alpaca unavailable")

    log.warning("%s — degrading to the in-memory SimBroker for DRY_RUN", err)
    return _sim(settings), BrokerStatus(broker="sim", degraded=True, detail=err)


def _probe(broker: Broker) -> str | None:
    """Return None if the broker answers, else a short failure description."""
    global _probe_cache
    now = time.monotonic()
    if _probe_cache is not None and now - _probe_cache[0] < _PROBE_TTL_SECONDS:
        return _probe_cache[1]
    try:
        broker.get_account()
        err = None
    except Exception as exc:  # noqa: BLE001 - any failure means "don't trust it"
        err = describe_broker_error(exc)
    _probe_cache = (now, err)
    return err


def _sim(settings: Settings) -> Broker:
    """One sim per process.

    A fresh instance per call would forget its positions between cycles. Note
    this is still per-*process*: the API container's sim is not the worker's, so
    read-only callers should prefer the equity ledger the worker writes.
    """
    global _sim_broker
    if _sim_broker is None:
        from sentinel.execution.sim_broker import SimBroker

        _sim_broker = SimBroker(cash=settings.starting_equity)
    return _sim_broker


def describe_broker_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "401" in text or "unauthorized" in lowered:
        return "Alpaca rejected the API credentials (HTTP 401)"
    if "403" in text or "forbidden" in lowered:
        return "Alpaca refused the request (HTTP 403) — check paper vs live keys"
    return f"Alpaca unreachable: {text}"
