"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sentinel import __version__
from sentinel.api.routers import (
    account,
    golive,
    health,
    performance,
    positions,
    risk,
    sentiment,
    signals,
    watchlist,
)
from sentinel.config import get_settings
from sentinel.logging_config import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Sentinel API",
        version=__version__,
        summary="Market-data ingestion & watchlist API (Phase 0)",
    )

    # Permissive CORS is fine for a single-user local dashboard; tighten before
    # any non-local deployment.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(watchlist.router)
    app.include_router(risk.router)
    app.include_router(signals.router)
    app.include_router(account.router)
    app.include_router(sentiment.router)
    app.include_router(performance.router)
    app.include_router(positions.router)
    app.include_router(golive.router)

    @app.get("/", tags=["ops"])
    def root() -> dict:
        return {"service": "sentinel", "version": __version__, "mode": settings.mode}

    return app


app = create_app()
