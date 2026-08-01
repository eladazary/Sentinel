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
    """A single watchlist entry.

    ``sector_etf`` is the sector ETF used for relative-strength features (e.g.
    XLK for tech names). Optional so a universe can be assembled before sectors
    are assigned.
    """

    symbol: str
    name: str
    sector_etf: str | None = None

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

    @field_validator("sector_etf")
    @classmethod
    def _normalize_sector(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        return v or None


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

    def sector_for(self, symbol: str) -> str | None:
        for t in self.tickers:
            if t.symbol == symbol:
                return t.sector_etf
        return None

    @property
    def sector_etfs(self) -> list[str]:
        """Distinct sector ETFs referenced by the watchlist."""
        return sorted({t.sector_etf for t in self.tickers if t.sector_etf})


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
    # Historical backfill source: "yfinance" (free, no keys) or "alpaca".
    backfill_source: str = "yfinance"

    # Market context symbols used for relative strength and regime features.
    benchmark_symbol: str = "SPY"
    vix_symbol: str = "^VIX"

    # --- Signal model (technical) ---
    # Predict P(positive excess return over this many trading days).
    label_horizon_days: int = Field(default=10, ge=1, le=60)
    # Walk-forward: retrain every N trading days, minimum train window in days.
    walkforward_train_days: int = Field(default=756, ge=60)  # ~3y
    walkforward_step_days: int = Field(default=63, ge=1)  # ~1 quarter
    model_dir: str = "artifacts/models"

    # --- Risk (see spec §6) ---
    default_risk_factor: int = Field(default=5, ge=1, le=10)
    # Hard, non-configurable breakers.
    daily_loss_breaker_pct: float = 3.0
    max_drawdown_breaker_pct: float = 12.0

    # --- Execution / backtest economics ---
    starting_equity: float = 100_000.0
    commission_per_share: float = 0.0
    slippage_bps: float = 5.0  # basis points applied to fills
    # Alpaca paper trading endpoint (live uses api.alpaca.markets).
    alpaca_paper: bool = True

    # --- Phase 2: news & social sentiment ---
    # Sentiment scorer: "finbert" (local, offline), "claude" (API), or "lexicon".
    sentiment_engine: str = "finbert"
    # Event classification: Ollama (local) if configured, else Claude (if key),
    # else rule-based. Ollama keeps the whole system self-hosted and key-free.
    ollama_model: str | None = None  # e.g. "llama3.1"; None disables Ollama
    ollama_base_url: str = "http://localhost:11434"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # News sources (free tiers; EDGAR needs no key, only a contact address).
    finnhub_api_key: str | None = None
    # SEC EDGAR's access policy requires a User-Agent carrying a real contact
    # email; it returns 403 for anything that looks like an unidentified bot.
    # The default below is deliberately invalid — see has_valid_sec_user_agent.
    sec_user_agent: str = "Sentinel/0.1 (contact: set SENTINEL_SEC_USER_AGENT)"

    # Social sources.
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "sentinel/0.1"
    reddit_subreddits: list[str] = Field(
        default_factory=lambda: ["stocks", "investing", "wallstreetbets"]
    )

    # Decay half-lives.
    news_half_life_days: float = 2.0
    earnings_news_half_life_days: float = 5.0
    social_half_life_hours: float = 24.0

    # Ensemble weights (spec §5). Social starts reduced until it has ~3 months of
    # live-shadow evidence (spec §8).
    # Where *live quotes* come from, independently of historical backfill.
    # This must not be yfinance: its get_latest_prices returns the most recent
    # daily close, so orders priced from it land on yesterday's number and never
    # fill. "auto" uses Alpaca (the execution venue, and a real trade feed) when
    # credentials exist, and falls back to yfinance so the dashboard still shows
    # something without keys.
    quote_source: str = "auto"

    # Spec §6 caps new positions per day (1/2/4 at risk 1/5/10). Turning this off
    # lets every signal that clears the conviction gate through on the same day.
    # DELIBERATE DEVIATION when false — the remaining limits are max_position_pct
    # per name, max_exposure_pct in total, and the daily-loss breaker, which
    # together still bound a bad day: ~5 positions at risk 7, and the 3% breaker
    # halts trading after roughly one and a half stop-outs.
    enforce_daily_position_cap: bool = True

    # --- worker liveness ---
    # Per-stage budgets. A blocking call without a socket timeout (alpaca-py
    # drives requests without one) otherwise stops the desk silently: the
    # container stays "healthy" and the only symptom is that nothing is written.
    stage_timeout_seconds: float = Field(default=120.0, ge=0)
    # Sentiment is the slow one: FinBERT inference plus EDGAR and social fetches
    # across the whole watchlist.
    sentiment_timeout_seconds: float = Field(default=300.0, ge=0)
    # Backstop for a hang SIGALRM can't interrupt: exit and let Docker restart.
    # Must exceed the slowest stage or it will fire on a merely slow cycle.
    worker_hard_timeout_seconds: float = Field(default=900.0, ge=0)

    # --- entry execution ---
    # How far above the live quote a marketable buy limit is placed. Big enough
    # to cross a normal spread, small enough to bound the worst fill. Entries
    # used to price off the last *daily close* with a 0.1% offset, which left
    # limits sitting below a market that had moved on.
    entry_limit_offset_pct: float = 0.25
    # Refuse to place an entry priced on a quote older than this. The worker
    # refreshes prices every cycle while the market is open, so anything much
    # older means the feed is broken, not that the stock is quiet.
    entry_max_quote_age_seconds: float = 300.0

    weight_technical: float = 0.45
    weight_news: float = 0.30
    weight_social: float = 0.10

    # Crowding: one-sided retail sentiment above this percentile is a contrarian
    # warning (spec §5C).
    crowding_threshold: float = 0.90
    # Earnings blackout: tighten risk within this many hours of earnings.
    earnings_blackout_hours: int = 48

    # --- Phase 3: alerting, dry-run gate, live guardrails ---
    # Alert channels activate when their config is present (log is always on).
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    slack_webhook_url: str | None = None

    # Go-live gate thresholds (spec §10.4).
    golive_min_trading_days: int = 60
    golive_min_reviews: int = 20
    # Paper max drawdown may be at most this multiple of the backtest expectation.
    golive_drawdown_tolerance: float = 1.5

    # Live guardrails (spec §10.5). Live is capped and cooled-off after breakers.
    live_capital_cap: float = 10_000.0
    live_cooloff_hours: int = 24

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

    @field_validator("backfill_source")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"yfinance", "alpaca"}:
            raise ValueError("backfill_source must be 'yfinance' or 'alpaca'")
        return v

    @field_validator("quote_source")
    @classmethod
    def _valid_quote_source(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"auto", "yfinance", "alpaca"}:
            raise ValueError("quote_source must be 'auto', 'yfinance' or 'alpaca'")
        return v

    @field_validator("sentiment_engine")
    @classmethod
    def _valid_sentiment(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"finbert", "claude", "lexicon"}:
            raise ValueError("sentiment_engine must be 'finbert', 'claude', or 'lexicon'")
        return v

    @property
    def has_valid_sec_user_agent(self) -> bool:
        """True when the UA carries a contact address SEC will accept.

        SEC 403s the shipped placeholder, which used to surface as an endless
        stream of "EDGAR fetch failed" warnings rather than a configuration
        error. A bare ``@`` check is enough: SEC only wants a reachable contact.
        """
        ua = (self.sec_user_agent or "").strip()
        if "set SENTINEL_SEC_USER_AGENT" in ua:
            return False
        return "@" in ua and "." in ua.split("@")[-1]

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_ollama(self) -> bool:
        return bool(self.ollama_model)

    @property
    def has_finnhub_key(self) -> bool:
        return bool(self.finnhub_api_key)

    @property
    def has_reddit_credentials(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    def load_watchlist(self) -> Watchlist:
        return load_watchlist(self.watchlist_path)

    def context_symbols(self, watchlist: Watchlist) -> list[str]:
        """All non-watchlist symbols needed for features (benchmark, sectors, VIX)."""
        extra = {self.benchmark_symbol, self.vix_symbol, *watchlist.sector_etfs}
        return sorted(extra - set(watchlist.symbols))

    def all_ingest_symbols(self, watchlist: Watchlist) -> list[str]:
        """Watchlist symbols plus the market-context symbols to backfill."""
        return watchlist.symbols + self.context_symbols(watchlist)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (safe to import anywhere)."""
    return Settings()
