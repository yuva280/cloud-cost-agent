from pydantic import BaseModel, Field
from datetime import datetime

class ServiceObservation(BaseModel):
    """Represents an observation of a cloud service's metrics at a specific point in time."""
    service_id: str = Field(..., description="Unique identifier or name of the service.")
    cpu_utilization_percent: float = Field(..., ge=0.0, le=100.0, description="CPU utilization percentage.")
    memory_utilization_percent: float = Field(..., ge=0.0, le=100.0, description="Memory utilization percentage.")
    traffic_rpm: int = Field(..., ge=0, description="Traffic in requests per minute.")
    latency_ms: float = Field(..., ge=0.0, description="Average or p99 latency in milliseconds.")
    cost_per_hour: float = Field(..., ge=0.0, description="Current estimated cost per hour.")
    observation_timestamp: datetime = Field(..., description="UTC timestamp when the observation was recorded.")
    state_version: str = Field(..., description="A unique version identifier for this state to detect stale data.")
    current_instances: int = Field(default=2, description="Current number of running instances.")
    min_instances: int = Field(default=1, description="Minimum number of running instances.")
    healthy: bool = Field(default=True, description="Whether the service is currently healthy.")
    is_critical: bool = Field(default=False, description="Whether the service is considered critical.")

