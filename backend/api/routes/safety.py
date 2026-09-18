"""Safety API endpoints for evaluating action proposals deterministically."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import get_safety_engine, get_state_manager
from backend.schemas.actions import ActionProposal
from backend.schemas.safety import SafetyCheckResult
from backend.schemas.service_state import ServiceState
from backend.services.safety_engine import DeterministicSafetyEngine, SafetyContext
from backend.services.state_manager import ServiceStateManager

router = APIRouter(prefix="/safety", tags=["Safety"])


class EvaluateSafetyRequest(BaseModel):
    """Payload for evaluating an action proposal."""

    proposal: ActionProposal
    override_state: Optional[ServiceState] = Field(
        None, description="Optional explicit state to evaluate against instead of live registry."
    )


class SafetyStatusResponse(BaseModel):
    """Safety and guardrail status for a service."""

    service_id: str
    is_protected: bool
    is_healthy: bool
    is_in_cooldown: bool
    cooldown_elapsed_seconds: float
    cooldown_remaining_seconds: float


@router.post("/evaluate", response_model=SafetyCheckResult)
def evaluate_proposal(
    req: EvaluateSafetyRequest,
    safety_engine: DeterministicSafetyEngine = Depends(get_safety_engine),
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> SafetyCheckResult:
    """Evaluate an ActionProposal against live telemetry or provided state.

    Deterministic policy evaluation (R01-R16).
    """
    target_state = req.override_state
    if target_state is None:
        target_state = state_manager.get_service(req.proposal.target_service_id)
        if not target_state:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target service '{req.proposal.target_service_id}' was not found in active registry.",
            )

    return safety_engine.evaluate(req.proposal, target_state)


@router.get("/status/{service_id}", response_model=SafetyStatusResponse)
def get_service_safety_status(
    service_id: str,
    safety_engine: DeterministicSafetyEngine = Depends(get_safety_engine),
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> SafetyStatusResponse:
    """Check guardrail status, critical flags, and cooldown timers for a service."""
    state = state_manager.get_service(service_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )

    now = datetime.now(timezone.utc)
    is_cooling, elapsed = safety_engine.is_in_cooldown(service_id, now)
    remaining = max(0.0, safety_engine.config.cooldown_seconds - elapsed) if is_cooling else 0.0
    is_protected = safety_engine.is_service_protected(
        service_id, is_explicitly_critical=state.is_critical
    )

    return SafetyStatusResponse(
        service_id=service_id,
        is_protected=is_protected,
        is_healthy=state.healthy,
        is_in_cooldown=is_cooling,
        cooldown_elapsed_seconds=round(elapsed, 1),
        cooldown_remaining_seconds=round(remaining, 1),
    )


@router.post("/cooldown/clear", status_code=status.HTTP_200_OK)
def clear_cooldown(
    service_id: Optional[str] = None,
    safety_engine: DeterministicSafetyEngine = Depends(get_safety_engine),
) -> dict:
    """Clear cooldown window for a specific service or all services."""
    safety_engine.clear_cooldown(service_id)
    target = service_id if service_id else "all services"
    return {"message": f"Cooldown cleared for {target}."}
