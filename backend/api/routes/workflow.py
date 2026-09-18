"""Workflow API endpoints for running end-to-end agent optimization loops and reporting."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import get_workflow_service
from backend.schemas.metrics import ServiceObservation
from backend.schemas.workflow import WorkflowReport
from backend.services.state_manager import ServiceNotFoundError
from backend.services.workflow_service import CostSavingsSummary, WorkflowService

router = APIRouter(prefix="/workflow", tags=["Workflow"])


class RunWorkflowRequest(BaseModel):
    """Payload to trigger an agent optimization workflow."""

    service_id: Optional[str] = Field(
        None, description="Service ID to fetch live observation from and optimize."
    )
    observation: Optional[ServiceObservation] = Field(
        None, description="Explicit point-in-time observation to run through workflow."
    )


@router.post("/run", response_model=WorkflowReport)
def run_workflow(
    req: RunWorkflowRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowReport:
    """Execute end-to-end autonomous agent workflow for a service or explicit observation."""
    if not req.service_id and not req.observation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'service_id' or 'observation' must be provided.",
        )
    try:
        return workflow_service.run_workflow(
            service_id=req.service_id,
            observation=req.observation,
        )
    except ServiceNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/reports", response_model=List[WorkflowReport])
def list_reports(
    service_id: Optional[str] = Query(None, description="Filter reports by target service ID."),
    limit: int = Query(50, ge=1, le=500, description="Max reports to return."),
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> List[WorkflowReport]:
    """Retrieve history of workflow execution reports."""
    return workflow_service.list_reports(service_id=service_id, limit=limit)


@router.get("/reports/{workflow_id}", response_model=WorkflowReport)
def get_report(
    workflow_id: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowReport:
    """Retrieve a single workflow report by its ID."""
    report = workflow_service.get_report(workflow_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow report '{workflow_id}' was not found.",
        )
    return report


@router.get("/savings", response_model=CostSavingsSummary)
def get_savings(
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> CostSavingsSummary:
    """Retrieve cumulative cost savings analytics and optimization metrics."""
    return workflow_service.get_savings_summary()


@router.delete("/reports", status_code=status.HTTP_204_NO_CONTENT)
def clear_reports(
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> None:
    """Clear all workflow history and savings metrics."""
    workflow_service.clear_history()
