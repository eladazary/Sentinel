"""Choose a broker: Alpaca (paper/live) when credentials exist, else the sim."""

from __future__ import annotations

from sentinel.config import Settings
from sentinel.execution.broker import Broker
from sentinel.logging_config import get_logger

log = get_logger(__name__)


def make_broker(settings: Settings) -> Broker:
    if settings.has_alpaca_credentials:
        from sentinel.execution.alpaca_broker import AlpacaBroker

        paper = settings.alpaca_paper or settings.mode == "DRY_RUN"
        log.info("using Alpaca broker (paper=%s)", paper)
        return AlpacaBroker(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=paper
        )

    from sentinel.execution.sim_broker import SimBroker

    log.warning("no Alpaca credentials — using in-memory SimBroker")
    return SimBroker(cash=settings.starting_equity)
