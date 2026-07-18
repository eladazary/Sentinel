# Sentinel

Focused stock signal & auto-trading system (dry-run first). See
[`docs/stock-signal-system-spec.md`](docs/stock-signal-system-spec.md) for the full
specification.

This repository currently implements **Phase 0**: ingestion + storage + backfill,
plus a watchlist UI reading live prices.

## Layout

```
sentinel/
├── docker-compose.yml      # api · worker · TimescaleDB · Redis · frontend
├── .env.example            # copy to .env and fill in Alpaca keys
├── backend/                # FastAPI API + ingestion worker (Python 3.12)
│   ├── config/watchlist.yaml
│   ├── migrations/         # Alembic (TimescaleDB hypertables)
│   ├── src/sentinel/       # application package
│   └── tests/
├── frontend/               # React + Vite dashboard (live prices)
└── docs/
```

## Quick start (Docker)

```bash
cp .env.example .env          # add ALPACA_API_KEY / ALPACA_SECRET_KEY
docker compose up --build
```

- API:       http://localhost:8000  (`/health`, `/watchlist`)
- Frontend:  http://localhost:5173

On first boot the worker backfills 5 years of daily bars for every watchlist
ticker, then polls the latest price on a fixed interval. Both are cached in
TimescaleDB (and Redis) and served to the UI via `/watchlist`.

## Local development

Backend (uses [uv](https://docs.astral.sh/uv/), Python 3.12):

```bash
cd backend
uv sync
uv run pytest                 # run the test suite
uv run uvicorn sentinel.api.app:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev                   # proxies /api -> http://localhost:8000
```

## Configuration

All runtime config comes from environment variables (see `.env.example`) plus the
watchlist file at `backend/config/watchlist.yaml`. See
`backend/src/sentinel/config.py` for the full list of settings.

## Status

Phase 0 only. No signal engine, risk manager, or execution yet — those are
Phases 1–2. Nothing here places orders.
