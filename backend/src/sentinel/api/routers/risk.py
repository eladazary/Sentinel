"""Risk endpoints — expose the exact Risk Factor → parameter mapping the engine
uses, so the UI's Risk Dial previews reality (spec §6, §9)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from sentinel.api.deps import settings_dep
from sentinel.config import Settings
from sentinel.risk.profile import risk_profile
from sentinel.schemas import RiskProfileOut, RiskProfilesResponse
from sentinel.state import get_risk_factor, set_risk_factor

router = APIRouter(prefix="/risk", tags=["risk"])


def _to_out(rf: int) -> RiskProfileOut:
    return RiskProfileOut(**risk_profile(rf).as_dict())


@router.get("/profiles", response_model=RiskProfilesResponse)
def profiles(settings: Settings = Depends(settings_dep)) -> RiskProfilesResponse:
    return RiskProfilesResponse(
        default_risk_factor=get_risk_factor(settings.default_risk_factor),
        profiles=[_to_out(rf) for rf in range(1, 11)],
    )


@router.get("/profile", response_model=RiskProfileOut)
def profile(risk_factor: int = Query(ge=1, le=10)) -> RiskProfileOut:
    return _to_out(risk_factor)


@router.put("/factor", response_model=RiskProfileOut)
def set_factor(risk_factor: int = Body(embed=True, ge=1, le=10)) -> RiskProfileOut:
    """Set the active Risk Factor; the trading loop picks it up next cycle."""
    return _to_out(set_risk_factor(risk_factor))
