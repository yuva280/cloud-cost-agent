"""Actions API endpoints for executing infrastructure changes and viewing audit history."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import get_execution_engine
from backend.schemas.actions import ActionProposal
from backend.schemas.execution import ExecutionResult
from backend.schemas.safety import SafetyCheckResult
from backend.services.execution_engine import ActionExecutionEngine

router = APIRouter(prefix="/actions", tags=["Actions"])


class ExecuteActionRequest(BaseModel):
    """Payload for executing an infrastructure action proposal."""

    proposal: ActionProposal
    safety_result: Optional[SafetyCheckResult] = Field(
        None, description="Pre-computed safety evaluation. If omitted, safety engine evaluates automatically."
    )
    enforce_freshness: bool = Field(
        True, description="Whether to reject if state version drifted between proposal and execution."
    )


class SimulateFailureRequest(BaseModel):
    """Payload for injecting simulated cloud provider errors."""

    service_id: str = Field(..., description="Target service ID or '*' for all services.")
    error_code: str = Field(..., description="Error code to simulate (e.g. 'capacity_unavailable').")


@router.post("/execute", response_model=ExecutionResult)
def execute_action(
    req: ExecuteActionRequest,
    execution_engine: ActionExecutionEngine = Depends(get_execution_engine),
) -> ExecutionResult:
    """Execute an approved infrastructure action proposal."""
    return execution_engine.execute(
        proposal=req.proposal,
        safety_result=req.safety_result,
        enforce_freshness=req.enforce_freshness,
    )


@router.get("/history", response_model=List[ExecutionResult])
def get_execution_history(
    service_id: Optional[str] = Query(None, description="Filter history by service ID."),
    execution_engine: ActionExecutionEngine = Depends(get_execution_engine),
) -> List[ExecutionResult]:
    """Retrieve audit history of executed actions."""
    return execution_engine.get_execution_history(service_id=service_id)


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_execution_history(
    execution_engine: ActionExecutionEngine = Depends(get_execution_engine),
) -> None:
    """Clear all records from the execution audit history."""
    execution_engine.clear_execution_history()


@router.post("/simulate-failure", status_code=status.HTTP_200_OK)
def set_simulated_failure(
    req: SimulateFailureRequest,
    execution_engine: ActionExecutionEngine = Depends(get_execution_engine),
) -> dict:
    """Configure a simulated provider failure for testing error paths."""
    execution_engine.set_simulated_failure(req.service_id, req.error_code)
    return {"message": f"Simulated failure '{req.error_code}' configured for '{req.service_id}'."}


@router.delete("/simulate-failure", status_code=status.HTTP_200_OK)
def clear_simulated_failures(
    service_id: Optional[str] = None,
    execution_engine: ActionExecutionEngine = Depends(get_execution_engine),
) -> dict:
    """Clear simulated provider failures."""
    execution_engine.clear_simulated_failures(service_id)
    target = service_id if service_id else "all services"
    return {"message": f"Simulated failures cleared for {target}."}
