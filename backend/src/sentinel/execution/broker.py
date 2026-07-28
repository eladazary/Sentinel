"""Broker abstraction.

A minimal, testable surface over "the account". The Alpaca adapter talks to the
paper/live API; the simulated adapter runs entirely in memory. Bracket orders
(entry + stop + take-profit) are the primary primitive so protection lives at the
broker even if Sentinel dies (spec §7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float


@dataclass
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry: float
    market_value: float


@dataclass
class OrderResult:
    id: str
    symbol: str
    qty: int
    side: str
    status: str


@dataclass
class WorkingOrder:
    """An order that is live at the broker but not yet filled.

    Distinct from a position: the decision log records intent at submit time, so
    without this an unfilled order reads as a holding that doesn't exist.
    """

    id: str
    symbol: str
    qty: int
    side: str
    status: str
    limit_price: float | None = None
    submitted_at: datetime | None = None


class Broker(Protocol):
    def get_account(self) -> AccountSnapshot: ...

    def get_positions(self) -> dict[str, BrokerPosition]: ...

    def open_order_keys(self) -> set[tuple[str, str]]:
        """Set of (symbol, side) for working orders — for the duplicate guard."""
        ...

    def open_orders(self) -> list[WorkingOrder]:
        """Working orders in full, for showing unfilled intent on the dashboard."""
        ...

    def submit_bracket(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult: ...

    def close_position(self, symbol: str) -> OrderResult | None: ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel one working order, e.g. a buy limit left behind by the market."""
        ...

    def cancel_all_orders(self) -> int: ...

    def close_all_positions(self) -> int: ...
