"""Runtime configuration.

All settings come from environment variables (prefix ``SENTINEL_``) with sane
defaults for local Docker Compose. The watchlist itself lives in a YAML file so
the trading universe can be edited without touching env vars.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Ticker(BaseModel):
    """A single watchlist entry."""

    symbol: str
    name: str

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("ticker symbol must not be empty")
        return v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


class Watchlist(BaseModel):
    """The hand-picked universe. The spec bounds this at 4–10 names; we allow
    1–10 so the system is still usable while a universe is being assembled."""

    tickers: list[Ticker] = Field(min_length=1, max_length=10)

    @field_validator("tickers")
    @classmethod
    def _no_duplicates(cls, tickers: list[Ticker]) -> list[Ticker]:
        seen = set()
        for t in tickers:
            if t.symbol in seen:
                raise ValueError(f"duplicate ticker in watchlist: {t.symbol}")
            seen.add(t.symbol)
        return tickers

    @property
    def symbols(self) -> list[str]:
        return [t.symbol for t in self.tickers]

    def name_for(self, symbol: str) -> str | None:
        for t in self.tickers:
            if t.symbol == symbol:
                return t.name
        return None


def load_watchlist(path: str | Path) -> Watchlist:
    """Load and validate the watchlist YAML file.

    Relative paths resolve against the current working directory, which is the
    backend/ dir locally and /app in the container (where config/ is copied).
    """
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise FileNotFoundError(f"watchlist file not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    return Watchlist.model_validate(data)


class Settings(BaseSettings):
    """Process configuration, loaded from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Operating mode. Phase 0 only reads market data; nothing trades.
    mode: str = "DRY_RUN"

    # Datastores.
    database_url: str = (
        "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel"
    )
    redis_url: str = "redis://localhost:6379/0"

    # Alpaca market data.
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    alpaca_data_feed: str = "iex"

    # Ingestion behaviour.
    backfill_years: int = Field(default=5, ge=1, le=20)
    ingest_interval_seconds: int = Field(default=60, ge=5)

    # Watchlist file location (relative paths resolve against backend/).
    watchlist_path: str = "config/watchlist.yaml"

    log_level: str = "INFO"

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in {"DRY_RUN", "LIVE"}:
            raise ValueError("mode must be DRY_RUN or LIVE")
        return v

    @field_validator("alpaca_data_feed")
    @classmethod
    def _valid_feed(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"iex", "sip"}:
            raise ValueError("alpaca_data_feed must be 'iex' or 'sip'")
        return v

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    def load_watchlist(self) -> Watchlist:
        return load_watchlist(self.watchlist_path)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (safe to import anywhere)."""
    return Settings()
