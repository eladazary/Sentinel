"""Pydantic response schemas for the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthComponent(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    mode: str
    version: str
    components: list[HealthComponent]


class WatchlistTicker(BaseModel):
    symbol: str
    name: str
    price: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    as_of: datetime | None
    stale: bool
    spark: list[float]


class WatchlistResponse(BaseModel):
    mode: str
    count: int
    tickers: list[WatchlistTicker]
