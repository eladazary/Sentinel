"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from sentinel.api.app import create_app
from sentinel.config import Ticker, Watchlist


@pytest.fixture
def sample_watchlist() -> Watchlist:
    return Watchlist(
        tickers=[
            Ticker(symbol="NVDA", name="NVIDIA"),
            Ticker(symbol="MSFT", name="Microsoft"),
        ]
    )


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def test_database_url() -> str:
    """URL for integration tests that need a real Postgres/TimescaleDB.

    Set ``SENTINEL_TEST_DATABASE_URL`` to enable them; otherwise those tests skip.
    """
    url = os.environ.get("SENTINEL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SENTINEL_TEST_DATABASE_URL not set; skipping DB integration test")
    return url
