"""Service State Manager.

Provides thread-safe in-memory infrastructure state management,
telemetry observation snapshots, optimistic concurrency control,
and default seed catalogs for cloud-cost optimization.
"""

from datetime import datetime, timezone
import re
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from backend.schemas.metrics import ServiceObservation
from backend.schemas.service_state import ServiceState


class StateManagerError(Exception):
    """Base exception for state manager operations."""


class ServiceNotFoundError(StateManagerError):
    """Raised when a requested service ID is not found in the registry."""


class ServiceAlreadyExistsError(StateManagerError):
    """Raised when attempting to register a service ID that already exists."""


class StateVersionConflictError(StateManagerError):
    """Raised when an update fails due to an optimistic concurrency version conflict."""


class InvalidStateUpdateError(StateManagerError):
    """Raised when a state update violates data or capacity constraints."""


def generate_next_version(current_version: str) -> str:
    """Generate the next state version string.

    If current version follows 'v{number}', it increments the integer.
    Otherwise, it appends or generates a unique hex suffix.
    """
    if not current_version:
        return "v1"

    match = re.fullmatch(r"v(\d+)", current_version.strip())
    if match:
        next_num = int(match.group(1)) + 1
        return f"v{next_num}"

    return f"v{uuid4().hex[:8]}"


class ServiceStateManager:
    """Thread-safe manager for tracking live infrastructure service states.

    Features:
    - Thread-safe in-memory registry with RLock.
    - Snapshot creation into ServiceObservation models for agents.
    - Optimistic concurrency control via expected_version checks.
    - Automatic state version advancement.
    - Seed services for realistic cloud cost optimization demos.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._services: Dict[str, ServiceState] = {}

    def _normalize_id(self, service_id: str) -> str:
        if not service_id or not service_id.strip():
            raise InvalidStateUpdateError("Service ID cannot be empty or whitespace.")
        return service_id.strip().lower()

    def register_service(
        self, state: ServiceState, allow_overwrite: bool = False
    ) -> ServiceState:
        """Register a new service in the repository.

        Args:
            state: The ServiceState to register.
            allow_overwrite: If True, overwrite if service already exists.

        Returns:
            The registered ServiceState.

        Raises:
            ServiceAlreadyExistsError: If service already exists and allow_overwrite is False.
        """
        norm_id = self._normalize_id(state.service_id)
        with self._lock:
            if norm_id in self._services and not allow_overwrite:
                raise ServiceAlreadyExistsError(
                    f"Service '{state.service_id}' is already registered."
                )
            stored_state = state.model_copy(update={"service_id": norm_id}, deep=True)
            self._services[norm_id] = stored_state
            return stored_state.model_copy(deep=True)

    def register_services(
        self, states: Iterable[ServiceState], allow_overwrite: bool = False
    ) -> None:
        """Register multiple services in bulk."""
        with self._lock:
            for state in states:
                self.register_service(state, allow_overwrite=allow_overwrite)

    def service_exists(self, service_id: str) -> bool:
        """Check if a service ID exists in the store."""
        try:
            norm_id = self._normalize_id(service_id)
        except InvalidStateUpdateError:
            return False
        with self._lock:
            return norm_id in self._services

    def get_service(self, service_id: str) -> Optional[ServiceState]:
        """Retrieve a service state by ID, or None if not found."""
        try:
            norm_id = self._normalize_id(service_id)
        except InvalidStateUpdateError:
            return None
        with self._lock:
            state = self._services.get(norm_id)
            return state.model_copy(deep=True) if state else None

    def get_service_or_raise(self, service_id: str) -> ServiceState:
        """Retrieve a service state by ID, or raise ServiceNotFoundError."""
        norm_id = self._normalize_id(service_id)
        with self._lock:
            state = self._services.get(norm_id)
            if not state:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")
            return state.model_copy(deep=True)

    def list_services(self) -> List[ServiceState]:
        """List copies of all registered service states."""
        with self._lock:
            return [s.model_copy(deep=True) for s in self._services.values()]

    def list_service_ids(self) -> List[str]:
        """List all registered service IDs."""
        with self._lock:
            return list(self._services.keys())

    def delete_service(self, service_id: str) -> bool:
        """Delete a service from the registry. Returns True if deleted, False if not found."""
        try:
            norm_id = self._normalize_id(service_id)
        except InvalidStateUpdateError:
            return False
        with self._lock:
            return self._services.pop(norm_id, None) is not None

    def create_observation(self, service_id: str) -> ServiceObservation:
        """Create a point-in-time ServiceObservation snapshot for agents.

        Args:
            service_id: Target service ID.

        Returns:
            ServiceObservation containing telemetry matching the current ServiceState.
        """
        state = self.get_service_or_raise(service_id)
        return ServiceObservation(
            service_id=state.service_id,
            cpu_utilization_percent=(
                state.cpu_utilization_percent if state.cpu_utilization_percent is not None else 0.0
            ),
            memory_utilization_percent=(
                state.memory_utilization_percent if state.memory_utilization_percent is not None else 0.0
            ),
            traffic_rpm=state.traffic_rpm,
            latency_ms=state.latency_ms,
            cost_per_hour=(
                state.cost_per_hour if state.cost_per_hour is not None else 0.0
            ),
            observation_timestamp=datetime.now(timezone.utc),
            state_version=state.state_version,
        )

    def update_state(
        self,
        service_id: str,
        new_state: ServiceState,
        expected_version: Optional[str] = None,
    ) -> ServiceState:
        """Update the full state of a service with optional optimistic version checking.

        Args:
            service_id: The target service ID.
            new_state: The new ServiceState model.
            expected_version: If provided, asserts current version matches before updating.

        Returns:
            The updated ServiceState.

        Raises:
            ServiceNotFoundError: If the service does not exist.
            StateVersionConflictError: If expected_version does not match current state version.
        """
        norm_id = self._normalize_id(service_id)
        with self._lock:
            current = self._services.get(norm_id)
            if not current:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

            if expected_version is not None and current.state_version != expected_version:
                raise StateVersionConflictError(
                    f"Version conflict for service '{service_id}': current version is "
                    f"'{current.state_version}', expected '{expected_version}'."
                )

            stored = new_state.model_copy(update={"service_id": norm_id}, deep=True)
            self._services[norm_id] = stored
            return stored.model_copy(deep=True)

    def advance_version(
        self, service_id: str, custom_version: Optional[str] = None
    ) -> str:
        """Advance the state version of a service.

        Args:
            service_id: Target service ID.
            custom_version: Optional explicit version string. If None, computes next version.

        Returns:
            The new state version.
        """
        norm_id = self._normalize_id(service_id)
        with self._lock:
            current = self._services.get(norm_id)
            if not current:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

            next_ver = (
                custom_version
                if custom_version is not None
                else generate_next_version(current.state_version)
            )
            updated = current.model_copy(update={"state_version": next_ver})
            self._services[norm_id] = updated
            return next_ver

    def update_metrics(
        self,
        service_id: str,
        cpu_utilization_percent: Optional[float] = None,
        memory_utilization_percent: Optional[float] = None,
        traffic_rpm: Optional[int] = None,
        latency_ms: Optional[float] = None,
        cost_per_hour: Optional[float] = None,
        healthy: Optional[bool] = None,
        bump_version: bool = True,
        custom_version: Optional[str] = None,
    ) -> ServiceState:
        """Update telemetry metrics on a service and optionally bump version.

        Args:
            service_id: Target service ID.
            cpu_utilization_percent: Updated CPU utilization percentage.
            memory_utilization_percent: Updated Memory utilization percentage.
            traffic_rpm: Updated traffic in RPM.
            latency_ms: Updated latency in milliseconds.
            cost_per_hour: Updated hourly cost.
            healthy: Updated health flag.
            bump_version: Whether to advance state_version.
            custom_version: Explicit version if bump_version is True.

        Returns:
            The updated ServiceState.
        """
        norm_id = self._normalize_id(service_id)
        with self._lock:
            current = self._services.get(norm_id)
            if not current:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

            updates: Dict[str, Any] = {}
            if cpu_utilization_percent is not None:
                if not (0.0 <= cpu_utilization_percent <= 100.0):
                    raise InvalidStateUpdateError("cpu_utilization_percent must be between 0.0 and 100.0.")
                updates["cpu_utilization_percent"] = cpu_utilization_percent

            if memory_utilization_percent is not None:
                if not (0.0 <= memory_utilization_percent <= 100.0):
                    raise InvalidStateUpdateError("memory_utilization_percent must be between 0.0 and 100.0.")
                updates["memory_utilization_percent"] = memory_utilization_percent

            if traffic_rpm is not None:
                if traffic_rpm < 0:
                    raise InvalidStateUpdateError("traffic_rpm cannot be negative.")
                updates["traffic_rpm"] = traffic_rpm

            if latency_ms is not None:
                if latency_ms < 0.0:
                    raise InvalidStateUpdateError("latency_ms cannot be negative.")
                updates["latency_ms"] = latency_ms

            if cost_per_hour is not None:
                if cost_per_hour < 0.0:
                    raise InvalidStateUpdateError("cost_per_hour cannot be negative.")
                updates["cost_per_hour"] = cost_per_hour

            if healthy is not None:
                updates["healthy"] = healthy

            if bump_version:
                updates["state_version"] = (
                    custom_version
                    if custom_version is not None
                    else generate_next_version(current.state_version)
                )

            updated_state = current.model_copy(update=updates, deep=True)
            self._services[norm_id] = updated_state
            return updated_state.model_copy(deep=True)

    def update_capacity(
        self,
        service_id: str,
        current_instances: Optional[int] = None,
        min_instances: Optional[int] = None,
        max_instances: Optional[int] = None,
        bump_version: bool = True,
        custom_version: Optional[str] = None,
    ) -> ServiceState:
        """Update instance capacity and operational bounds.

        Args:
            service_id: Target service ID.
            current_instances: New instance count.
            min_instances: New minimum instance limit.
            max_instances: New maximum instance limit.
            bump_version: Whether to advance state_version.
            custom_version: Explicit version if bump_version is True.

        Returns:
            The updated ServiceState.
        """
        norm_id = self._normalize_id(service_id)
        with self._lock:
            current = self._services.get(norm_id)
            if not current:
                raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

            target_min = min_instances if min_instances is not None else current.min_instances
            target_max = max_instances if max_instances is not None else current.max_instances
            target_curr = current_instances if current_instances is not None else current.current_instances

            if target_min < 0:
                raise InvalidStateUpdateError("min_instances cannot be negative.")
            if target_max < 1:
                raise InvalidStateUpdateError("max_instances must be at least 1.")
            if target_min > target_max:
                raise InvalidStateUpdateError(
                    f"min_instances ({target_min}) cannot exceed max_instances ({target_max})."
                )
            if target_curr < 0:
                raise InvalidStateUpdateError("current_instances cannot be negative.")

            updates: Dict[str, Any] = {
                "current_instances": target_curr,
                "min_instances": target_min,
                "max_instances": target_max,
            }

            if bump_version:
                updates["state_version"] = (
                    custom_version
                    if custom_version is not None
                    else generate_next_version(current.state_version)
                )

            updated_state = current.model_copy(update=updates, deep=True)
            self._services[norm_id] = updated_state
            return updated_state.model_copy(deep=True)

    def reset(self) -> None:
        """Clear all registered services."""
        with self._lock:
            self._services.clear()

    def seed_default_services(self) -> List[ServiceState]:
        """Seed a default catalog of realistic cloud services for demo and testing.

        Services seeded:
        1. 'cart-service': Overprovisioned web service (ideal for scale-down).
        2. 'auth-service': Critical authentication service.
        3. 'payment-gateway': Critical payment API.
        4. 'analytics-worker': Idle batch worker (ideal for stop_idle_service / delay_batch).
        5. 'prod-db': Critical primary database (protected from destructive actions).
        6. 'spiky-api': Web service near SLA latency ceiling (scale-down rejected by safety).

        Returns:
            List of seeded ServiceState objects.
        """
        defaults = [
            ServiceState(
                service_id="cart-service",
                current_instances=4,
                min_instances=1,
                max_instances=10,
                latency_ms=45.0,
                max_latency_ms=200.0,
                traffic_rpm=150,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=12.0,
                memory_utilization_percent=25.0,
                cost_per_hour=1.60,
                service_type="web",
                is_critical=False,
            ),
            ServiceState(
                service_id="auth-service",
                current_instances=3,
                min_instances=2,
                max_instances=8,
                latency_ms=25.0,
                max_latency_ms=100.0,
                traffic_rpm=4500,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=65.0,
                memory_utilization_percent=55.0,
                cost_per_hour=1.20,
                service_type="web",
                is_critical=True,
            ),
            ServiceState(
                service_id="payment-gateway",
                current_instances=5,
                min_instances=3,
                max_instances=12,
                latency_ms=60.0,
                max_latency_ms=150.0,
                traffic_rpm=2200,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=50.0,
                memory_utilization_percent=40.0,
                cost_per_hour=2.50,
                service_type="api",
                is_critical=True,
            ),
            ServiceState(
                service_id="analytics-worker",
                current_instances=2,
                min_instances=0,
                max_instances=6,
                latency_ms=15.0,
                max_latency_ms=500.0,
                traffic_rpm=0,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=2.0,
                memory_utilization_percent=8.0,
                cost_per_hour=0.80,
                service_type="batch",
                is_critical=False,
            ),
            ServiceState(
                service_id="prod-db",
                current_instances=2,
                min_instances=2,
                max_instances=4,
                latency_ms=10.0,
                max_latency_ms=50.0,
                traffic_rpm=8000,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=70.0,
                memory_utilization_percent=80.0,
                cost_per_hour=4.50,
                service_type="database",
                is_critical=True,
            ),
            ServiceState(
                service_id="spiky-api",
                current_instances=2,
                min_instances=1,
                max_instances=8,
                latency_ms=195.0,
                max_latency_ms=200.0,
                traffic_rpm=1200,
                healthy=True,
                state_version="v1",
                cpu_utilization_percent=75.0,
                memory_utilization_percent=70.0,
                cost_per_hour=0.90,
                service_type="web",
                is_critical=False,
            ),
        ]
        with self._lock:
            for s in defaults:
                self.register_service(s, allow_overwrite=True)
            return self.list_services()
