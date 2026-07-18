"""FastAPI dependencies. A DB session per request, plus settings/watchlist."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from sentinel.config import Settings, Watchlist, get_settings
from sentinel.db import get_sessionmaker


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def settings_dep() -> Settings:
    return get_settings()


def watchlist_dep() -> Watchlist:
    return get_settings().load_watchlist()
