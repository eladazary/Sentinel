"""In-memory simulated broker.

Used for offline tests and as a fallback when no Alpaca credentials are present.
Fills market/limit entries immediately at the last price and tracks bracket
stop/take-profit levels, which are triggered by ``mark(prices)`` calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

from sentinel.execution.broker import AccountSnapshot, BrokerPosition, OrderResult


@dataclass
class _SimPosition:
    symbol: str
    qty: int
    avg_entry: float
    stop: float
    take_profit: float


@dataclass
class SimBroker:
    cash: float = 100_000.0
    last_prices: dict[str, float] = field(default_factory=dict)
    positions: dict[str, _SimPosition] = field(default_factory=dict)
    _ids: "count[int]" = field(default_factory=lambda: count(1))
    fills: list[OrderResult] = field(default_factory=list)

    # ---- market data the sim needs ----
    def set_prices(self, prices: dict[str, float]) -> None:
        self.last_prices.update(prices)

    # ---- Broker protocol ----
    def get_account(self) -> AccountSnapshot:
        equity = self.cash + sum(
            p.qty * self.last_prices.get(p.symbol, p.avg_entry)
            for p in self.positions.values()
        )
        return AccountSnapshot(equity=equity, cash=self.cash, buying_power=self.cash)

    def get_positions(self) -> dict[str, BrokerPosition]:
        out: dict[str, BrokerPosition] = {}
        for p in self.positions.values():
            px = self.last_prices.get(p.symbol, p.avg_entry)
            out[p.symbol] = BrokerPosition(p.symbol, p.qty, p.avg_entry, p.qty * px)
        return out

    def open_order_keys(self) -> set[tuple[str, str]]:
        # Sim fills instantly, so there are never working orders.
        return set()

    def submit_bracket(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult:
        cost = qty * limit_price
        if cost > self.cash:
            raise ValueError("insufficient cash in sim broker")
        self.cash -= cost
        self.positions[symbol] = _SimPosition(
            symbol, qty, limit_price, stop_price, take_profit
        )
        self.last_prices[symbol] = limit_price
        oid = f"sim-{next(self._ids)}"
        res = OrderResult(oid, symbol, qty, "buy", "filled")
        self.fills.append(res)
        return res

    def close_position(self, symbol: str) -> OrderResult | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        px = self.last_prices.get(symbol, pos.avg_entry)
        self.cash += pos.qty * px
        oid = f"sim-{next(self._ids)}"
        res = OrderResult(oid, symbol, pos.qty, "sell", "filled")
        self.fills.append(res)
        return res

    def cancel_all_orders(self) -> int:
        return 0  # nothing ever working

    def close_all_positions(self) -> int:
        n = len(self.positions)
        for symbol in list(self.positions):
            self.close_position(symbol)
        return n

    # ---- sim-only: trigger brackets ----
    def mark(self, prices: dict[str, float]) -> list[str]:
        """Update prices and auto-close positions whose stop/target is hit.

        Returns the symbols that were closed.
        """
        self.set_prices(prices)
        closed: list[str] = []
        for symbol, pos in list(self.positions.items()):
            px = prices.get(symbol)
            if px is None:
                continue
            if px <= pos.stop or px >= pos.take_profit:
                self.close_position(symbol)
                closed.append(symbol)
        return closed
