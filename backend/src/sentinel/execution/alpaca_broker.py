"""Alpaca broker adapter (paper or live).

Paper and live share one API — the only difference is ``paper=True`` and the
credentials — so the dry-run→live flip is a config change, nothing more (spec §7).
"""

from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from sentinel.execution.broker import (
    AccountSnapshot,
    BrokerPosition,
    OrderResult,
    WorkingOrder,
)


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        if not api_key or not secret_key:
            raise ValueError("Alpaca credentials are required")
        self._client = TradingClient(api_key, secret_key, paper=paper)
        self.paper = paper

    def get_account(self) -> AccountSnapshot:
        a = self._client.get_account()
        return AccountSnapshot(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
        )

    def get_positions(self) -> dict[str, BrokerPosition]:
        out: dict[str, BrokerPosition] = {}
        for p in self._client.get_all_positions():
            out[p.symbol] = BrokerPosition(
                symbol=p.symbol,
                qty=int(float(p.qty)),
                avg_entry=float(p.avg_entry_price),
                market_value=float(p.market_value),
            )
        return out

    def open_order_keys(self) -> set[tuple[str, str]]:
        orders = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        return {(o.symbol, o.side.value.lower()) for o in orders}

    def open_orders(self) -> list[WorkingOrder]:
        orders = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        out: list[WorkingOrder] = []
        for o in orders:
            limit = getattr(o, "limit_price", None)
            out.append(
                WorkingOrder(
                    id=str(o.id),
                    symbol=o.symbol,
                    qty=int(float(o.qty or 0)),
                    side=o.side.value.lower(),
                    status=str(getattr(o.status, "value", o.status)),
                    limit_price=float(limit) if limit is not None else None,
                    submitted_at=getattr(o, "submitted_at", None),
                )
            )
        return out

    def submit_bracket(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit: float,
    ) -> OrderResult:
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        o = self._client.submit_order(req)
        return OrderResult(
            id=str(o.id), symbol=symbol, qty=qty, side="buy", status=str(o.status)
        )

    def close_position(self, symbol: str) -> OrderResult | None:
        try:
            o = self._client.close_position(symbol)
        except Exception:
            return None
        return OrderResult(
            id=str(getattr(o, "id", "")),
            symbol=symbol,
            qty=int(float(getattr(o, "qty", 0) or 0)),
            side="sell",
            status=str(getattr(o, "status", "accepted")),
        )

    def cancel_order(self, order_id: str) -> bool:
        self._client.cancel_order_by_id(order_id)
        return True

    def cancel_all_orders(self) -> int:
        return len(self._client.cancel_orders() or [])

    def close_all_positions(self) -> int:
        results = self._client.close_all_positions(cancel_orders=True) or []
        return len(results)
