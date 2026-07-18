"""Database engine and session management (SQLAlchemy 2.0, sync)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    # pool_pre_ping keeps long-lived worker connections healthy across restarts
    # of the database container.
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_sessionmaker() -> sessionmaker[Session]:
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context manager."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
