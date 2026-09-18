"""Services API endpoints for managing infrastructure state and telemetry."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import get_state_manager, reset_dependencies
from backend.schemas.metrics import ServiceObservation
from backend.schemas.service_state import ServiceState
from backend.services.state_manager import (
    InvalidStateUpdateError,
    ServiceAlreadyExistsError,
    ServiceNotFoundError,
    ServiceStateManager,
)

router = APIRouter(prefix="/services", tags=["Services"])


class UpdateMetricsRequest(BaseModel):
    """Payload for updating telemetry metrics on a service."""

    cpu_utilization_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    memory_utilization_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    traffic_rpm: Optional[int] = Field(None, ge=0)
    latency_ms: Optional[float] = Field(None, ge=0.0)
    cost_per_hour: Optional[float] = Field(None, ge=0.0)
    healthy: Optional[bool] = None
    bump_version: bool = True
    custom_version: Optional[str] = None


class UpdateCapacityRequest(BaseModel):
    """Payload for updating capacity bounds and active instance count."""

    current_instances: Optional[int] = Field(None, ge=0)
    min_instances: Optional[int] = Field(None, ge=0)
    max_instances: Optional[int] = Field(None, ge=1)
    bump_version: bool = True
    custom_version: Optional[str] = None


@router.get("", response_model=List[ServiceState])
def list_services(
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> List[ServiceState]:
    """Retrieve all registered services and their active states."""
    return state_manager.list_services()


@router.get("/{service_id}", response_model=ServiceState)
def get_service(
    service_id: str,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> ServiceState:
    """Retrieve a single service state by ID."""
    state = state_manager.get_service(service_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )
    return state


@router.get("/{service_id}/observation", response_model=ServiceObservation)
def get_service_observation(
    service_id: str,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> ServiceObservation:
    """Generate a point-in-time ServiceObservation telemetry snapshot for an agent."""
    try:
        return state_manager.create_observation(service_id)
    except ServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )


@router.post("", response_model=ServiceState, status_code=status.HTTP_201_CREATED)
def register_service(
    state: ServiceState,
    allow_overwrite: bool = False,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> ServiceState:
    """Register a new service in the state repository."""
    try:
        return state_manager.register_service(state, allow_overwrite=allow_overwrite)
    except ServiceAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidStateUpdateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{service_id}/metrics", response_model=ServiceState)
def update_metrics(
    service_id: str,
    req: UpdateMetricsRequest,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> ServiceState:
    """Update telemetry metrics on a service."""
    try:
        return state_manager.update_metrics(
            service_id=service_id,
            cpu_utilization_percent=req.cpu_utilization_percent,
            memory_utilization_percent=req.memory_utilization_percent,
            traffic_rpm=req.traffic_rpm,
            latency_ms=req.latency_ms,
            cost_per_hour=req.cost_per_hour,
            healthy=req.healthy,
            bump_version=req.bump_version,
            custom_version=req.custom_version,
        )
    except ServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )
    except InvalidStateUpdateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{service_id}/capacity", response_model=ServiceState)
def update_capacity(
    service_id: str,
    req: UpdateCapacityRequest,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> ServiceState:
    """Update instance capacity and bounds on a service."""
    try:
        return state_manager.update_capacity(
            service_id=service_id,
            current_instances=req.current_instances,
            min_instances=req.min_instances,
            max_instances=req.max_instances,
            bump_version=req.bump_version,
            custom_version=req.custom_version,
        )
    except ServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )
    except InvalidStateUpdateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: str,
    state_manager: ServiceStateManager = Depends(get_state_manager),
) -> None:
    """Delete a service from the registry."""
    if not state_manager.delete_service(service_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' was not found.",
        )


@router.post("/reset", response_model=List[ServiceState])
def reset_services() -> List[ServiceState]:
    """Reset all in-memory services to the default seed catalog."""
    reset_dependencies()
    return get_state_manager().list_services()
