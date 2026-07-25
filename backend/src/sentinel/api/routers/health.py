"""Health endpoint: reports overall status plus per-component checks."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from sentinel import __version__
from sentinel.config import get_settings
from sentinel.db import get_engine
from sentinel.redis_client import ping as redis_ping
from sentinel.schemas import HealthComponent, HealthResponse

router = APIRouter(tags=["ops"])


def _check_db() -> HealthComponent:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return HealthComponent(name="database", ok=True)
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        return HealthComponent(name="database", ok=False, detail=str(exc))


def _check_redis() -> HealthComponent:
    ok = redis_ping()
    return HealthComponent(
        name="redis", ok=ok, detail=None if ok else "ping failed"
    )


def _check_broker() -> HealthComponent:
    """Report the broker without gating liveness.

    A rejected API key is worth seeing here, but it must not turn the container
    unhealthy — DRY_RUN carries on against the sim, and flipping to 503 would
    take the API down and block the worker's service_healthy dependency.
    """
    from sentinel.execution.factory import BrokerUnavailable, make_broker_with_status

    try:
        _, status = make_broker_with_status(get_settings())
    except BrokerUnavailable as exc:
        return HealthComponent(name="broker", ok=False, detail=str(exc))
    return HealthComponent(
        name=f"broker:{status.broker}", ok=not status.degraded, detail=status.detail
    )


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    settings = get_settings()
    gating = [_check_db(), _check_redis()]
    all_ok = all(c.ok for c in gating)
    if not all_ok:
        # 503 so orchestrators/monitors can react, but the body still details why.
        response.status_code = 503
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        mode=settings.mode,
        version=__version__,
        components=[*gating, _check_broker()],
    )
