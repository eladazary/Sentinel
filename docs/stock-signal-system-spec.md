# Sentinel — Focused Stock Signal & Auto-Trading System
**Version 0.1 — Specification (Dry-Run First)**

---

## 1. What we're building

A single-user system that watches a small, hand-picked universe of **4–10 large-cap stocks**, continuously fuses three evidence streams — **price/technical behavior, news, and social sentiment from curated Twitter/X + Reddit accounts** — into a per-stock conviction score, and converts that score into **buy/sell/hold recommendations sized by a user-controlled Risk Factor (1–10)**.

It launches in **dry-run (paper trading)** mode with full telemetry, and only after passing acceptance criteria (Section 10) can be flipped to live execution.

**Honest framing (read this first):** No system reliably predicts stock prices; large-cap equities are heavily arbitraged by institutions with better data and lower latency. The realistic goal is a *disciplined, evidence-fused, risk-controlled* process — not a money printer. The dry-run phase exists to prove (or disprove) edge before a single real dollar moves. All hard risk limits below are non-negotiable in code, not settings.

---

## 2. Scope

- Universe: 4–10 tickers, user-editable (default suggestion: liquid Fortune-100 names, e.g. AAPL, MSFT, NVDA, AMZN, JPM, XOM — final list is the user's call)
- Horizon: swing trading, 2 days – 6 weeks per position (this horizon is where news + sentiment plausibly matter; intraday HFT is explicitly out of scope)
- Long-only in v1 (shorting, options deferred to v2)
- One brokerage account, US equities, market hours execution only
- Modes: `DRY_RUN` (default, paper) → `LIVE` (manual, two-step unlock)

---

## 3. Architecture overview

```
 ┌─────────────────────────────────────────────────────┐
 │                    INGESTION LAYER                   │
 │  Prices/Volume   News/Filings   Social (X + Reddit)  │
 └────────┬──────────────┬───────────────┬─────────────┘
          ▼              ▼               ▼
 ┌─────────────────────────────────────────────────────┐
 │           FEATURE STORE (TimescaleDB)                │
 │  OHLCV bars · indicators · NLP sentiment scores      │
 └────────────────────────┬────────────────────────────┘
                          ▼
 ┌─────────────────────────────────────────────────────┐
 │                 SIGNAL ENGINE                        │
 │  Technical model + News model + Social model         │
 │  → weighted ensemble → conviction score (−100..+100) │
 └────────────────────────┬────────────────────────────┘
                          ▼
 ┌─────────────────────────────────────────────────────┐
 │              RISK MANAGER (Risk Factor 1–10)         │
 │  position sizing · stops · exposure caps · breakers  │
 └────────────────────────┬────────────────────────────┘
                          ▼
 ┌──────────────────────────────┐   ┌──────────────────┐
 │  EXECUTION (Alpaca paper/live)│◄──│  KILL SWITCH     │
 └────────────────────────┬─────┘   └──────────────────┘
                          ▼
 ┌─────────────────────────────────────────────────────┐
 │        UI DASHBOARD (React) + Alerting (push)        │
 └─────────────────────────────────────────────────────┘
```

---

## 4. Data: what we fetch, from where, how often

### 4.1 Market data (prices, volume)
| Data | Source | Cadence | Notes |
|---|---|---|---|
| Historical daily OHLCV (5+ yrs) | Polygon.io or Alpaca Market Data; yfinance as free backfill | one-time backfill + nightly | backtesting foundation |
| Intraday bars (1-min/5-min) | Alpaca Market Data (comes free with the brokerage account) or Polygon | streaming/websocket during market hours | signal refresh |
| Corporate actions (splits, dividends) | Polygon / Alpha Vantage | nightly | adjust history correctly |
| VIX & sector ETF prices (SPY, QQQ, sector) | same providers | 5-min | market-regime context |

### 4.2 News & filings
| Data | Source | Cadence |
|---|---|---|
| Ticker-tagged news headlines + bodies | Finnhub news API (good free tier) and/or Benzinga, Marketaux | poll every 2–5 min |
| Earnings calendar, EPS estimates vs. actuals | Finnhub / Financial Modeling Prep | daily |
| SEC filings (8-K, 10-Q, insider Form 4) | SEC EDGAR full-text API (free, official) | poll every 10 min |
| Analyst rating changes | Finnhub / Benzinga | daily |

### 4.3 Social sentiment (curated humans, not the whole firehose)
Following the whole internet is noise; we follow a **curated tracker list of 20–60 accounts**, each with a per-account credibility score that is *earned from measured accuracy*, not follower count.

| Data | Source | Cadence | Notes |
|---|---|---|---|
| Posts from tracked X/Twitter accounts | X API v2 (Basic tier, paid ~$200/mo) — filtered stream / user timelines | near-real-time | fintwit accounts you select in the UI |
| Reddit posts/comments | Official Reddit Data API (PRAW) — r/stocks, r/investing, r/wallstreetbets, ticker subs | poll every 5 min | track both specific users and ticker mentions |
| StockTwits per-ticker stream | StockTwits public API | every 5 min | comes pre-labeled bullish/bearish |

**Tracker-list mechanics:** every tracked account gets a rolling **hit-rate ledger** — when they post a directional call on one of our tickers, we log it and score the outcome at +5 and +20 trading days. Accounts with a track record get weight; accounts that are just loud decay toward zero weight automatically. The UI lets you add/remove/pin accounts. (We do not name specific individuals in this spec; you'll pick your own list, and the system will tell you who among them is actually worth following.)

**Compliance note:** X and Reddit API terms restrict redistribution and some automated uses — the system stores derived scores and short excerpts for personal use, not full mirrored content.

### 4.4 What we deliberately do NOT ingest
Unverified Telegram/Discord pump groups, paid "signal services," and anything that smells like material non-public information. Trading on MNPI is illegal; the ingestion layer has a hard filter policy and everything is logged for auditability.

---

## 5. Signal engine

Three sub-models, each producing a per-ticker score in [−100, +100] plus a confidence in [0, 1]:

**A. Technical model** (weight ~45% default)
- Features: 20/50/200-day MA crossovers and distance, RSI(14), MACD histogram, Bollinger %B, ATR(14), volume z-score vs. 30-day, gap behavior, relative strength vs. SPY and vs. sector ETF, market regime flag (VIX percentile).
- Model: gradient-boosted trees (LightGBM) trained walk-forward to predict **probability of positive excess return over the next 10 trading days** — a direction/probability target, *not* a price target. LSTM/transformer price prediction is explicitly rejected for v1: it demos well and fails out-of-sample.

**B. News model** (weight ~30% default)
- Every headline/body is scored by **FinBERT** (finance-tuned sentiment) plus an **LLM pass (Claude API)** that classifies: event type (earnings, guidance, litigation, product, macro, M&A), materiality (1–5), direction, and novelty (is this actually new vs. rehash).
- Decay function: news impact half-life ~2 trading days, earnings events ~5.
- Earnings blackout logic: risk manager tightens automatically 48h before earnings.

**C. Social model** (weight ~25% default)
- Per-post: FinBERT sentiment × author credibility weight × recency decay × engagement z-score (abnormal engagement matters, raw likes don't).
- Aggregated to per-ticker social momentum + a **crowding flag** (extreme one-sided retail sentiment is treated as a *contrarian* warning above a threshold, based on well-documented meme-stock dynamics).

**Ensemble:** conviction = Σ(weightᵢ × scoreᵢ × confidenceᵢ), weights re-fit quarterly from walk-forward attribution. Disagreement between models lowers effective confidence. Every recommendation ships with a **plain-English explanation** listing its top 3 drivers — no black-box trades.

---

## 6. Risk Factor: the dial you'll turn

A single integer **1–10** set in the UI, mapped to concrete, code-enforced parameters:

| Parameter | Risk 1 (defensive) | Risk 5 | Risk 10 (aggressive) |
|---|---|---|---|
| Max position size (% of equity) | 5% | 12% | 20% |
| Max total exposure | 30% | 70% | 95% |
| Min conviction to open | ≥ 70 | ≥ 50 | ≥ 35 |
| Stop-loss (ATR multiples) | 1.5× | 2.5× | 3.5× |
| Trade around earnings | never | reduced size | allowed |
| Max new positions/day | 1 | 2 | 4 |

Intermediate values interpolate linearly. Changing the dial **never retroactively violates** an open position's stop; it only governs new decisions.

**Hard limits independent of the dial (cannot be configured away):**
- Daily loss circuit breaker: −3% of equity → flat, halt, notify
- Max drawdown breaker: −12% from high-water mark → system locks to DRY_RUN until manually reviewed
- Per-order sanity checks: price collar, max share count, duplicate-order guard
- Physical **kill switch** in the UI: one tap → cancel all open orders, optional flatten

---

## 7. Execution

- **Broker: Alpaca** — chosen because paper and live trading share an identical API, so the dry-run→live flip changes an endpoint + key, nothing else. (Interactive Brokers is the v2 alternative if you outgrow it.)
- Orders: limit orders with marketable offsets by default; bracket orders (entry + stop + take-profit) so protection lives at the broker even if our system dies.
- Scheduler: signals recompute every 15 min during market hours; entries only in 10:00–15:30 ET window (skip open/close chaos).
- Every decision — fired or skipped — is written to an immutable decision log with full feature snapshot, for post-mortems.

---

## 8. Backtesting & validation (before dry-run even starts)

- Walk-forward backtest over ≥ 4 years including 2022 (bear) and 2020 (crash), with realistic slippage + commission model.
- No lookahead: news/social timestamps aligned to actual availability; point-in-time data only.
- Benchmarks: buy-and-hold of the same basket, and SPY. Report CAGR, Sharpe, Sortino, max drawdown, win rate, exposure-adjusted return.
- Social-model caveat: historical X/Reddit data is limited on cheap tiers — the social model may only be validated properly during the dry-run itself. Its ensemble weight starts reduced until it has 3 months of live-shadow evidence.

---

## 9. UI (React dashboard)

- **Header:** mode badge (DRY RUN / LIVE), paper equity, day P&L, kill switch.
- **Risk Dial:** the signature control — a 1–10 gauge that live-previews exactly what each setting means (max position, stop width, conviction gate) before you commit it.
- **Watchlist grid (4–10 cards):** price + sparkline, current conviction score with per-model breakdown (technical/news/social), active signal chip (BUY/HOLD/TRIM/SELL) with confidence.
- **Signal feed:** chronological recommendations with the 3-driver explanation, and what the system did (opened, sized, skipped-and-why).
- **Sentiment desk:** live feed from tracked accounts, each tagged with stance, ticker, and that author's measured hit rate; add/remove trackers here.
- **Positions & performance:** open positions with stops visualized, equity curve vs. SPY, drawdown chart, per-model attribution.
- **Settings:** universe editor, risk dial, mode switch (LIVE requires typed confirmation + 24h cool-off after any breaker event).
- Mobile-responsive; push/Telegram alerts for signals, fills, and breaker events.

A working visual prototype of this dashboard ships alongside this spec.

---

## 10. Rollout phases & go-live gate

1. **Phase 0 (wks 1–2):** ingestion + storage + backfill; watchlist UI reading live data.
2. **Phase 1 (wks 3–5):** technical model + backtester; risk manager; paper execution loop.
3. **Phase 2 (wks 6–8):** news + social pipelines, tracker ledger, full ensemble; dashboard complete.
4. **Phase 3 (dry-run, minimum 3 months):** full system on paper. Gate to LIVE requires **all** of: ≥ 60 trading days, positive risk-adjusted excess return vs. basket buy-and-hold, max drawdown within model expectation, zero breaker malfunctions, and manual review of 20 random decision logs.
5. **Phase 4 (live, capped):** start with a small capital cap regardless of confidence; scale only on continued evidence.

## 11. Tech stack

Python 3.12 · FastAPI · TimescaleDB (Postgres) · Redis (queues/cache) · LightGBM · FinBERT (HuggingFace) · Claude API (news/event classification) · Alpaca SDK · React + Recharts frontend · Docker Compose · deployed on a small VPS with uptime monitoring.

**Estimated running costs:** X API Basic ~$200/mo (the big one — Reddit + StockTwits + Finnhub free tiers may suffice to start without it), Polygon starter ~$30/mo or $0 with Alpaca data, VPS ~$20/mo, LLM API ~$10–30/mo at this volume.

## 12. Legal & risk disclosures

This is a personal decision-support and automation tool, not investment advice, and I'm not a financial advisor. Automated trading can lose money faster than manual trading; past backtest performance does not predict future results. You remain responsible for tax reporting, pattern-day-trader rules (irrelevant at swing horizon but coded as a guard anyway), and broker/API terms of service. Never fund it with money you can't afford to lose.