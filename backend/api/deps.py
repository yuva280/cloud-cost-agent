"""FastAPI dependency injection providers for backend singletons."""

from typing import Tuple

from backend.services.execution_engine import ActionExecutionEngine
from backend.services.safety_engine import DeterministicSafetyEngine
from backend.services.state_manager import ServiceStateManager
from backend.services.workflow_service import WorkflowService
from backend.simulator.cloud_simulator import CloudEnvironmentSimulator
from backend.simulator.scenarios import ScenarioType

# Module-level singletons
_state_manager = ServiceStateManager()
_safety_engine = DeterministicSafetyEngine()
_execution_engine = ActionExecutionEngine(
    state_manager=_state_manager, safety_engine=_safety_engine
)
_simulator = CloudEnvironmentSimulator(state_manager=_state_manager)
_workflow_service = WorkflowService(
    state_manager=_state_manager,
    execution_engine=_execution_engine,
    safety_engine=_safety_engine,
)


def get_state_manager() -> ServiceStateManager:
    """Dependency provider for ServiceStateManager."""
    return _state_manager


def get_safety_engine() -> DeterministicSafetyEngine:
    """Dependency provider for DeterministicSafetyEngine."""
    return _safety_engine


def get_execution_engine() -> ActionExecutionEngine:
    """Dependency provider for ActionExecutionEngine."""
    return _execution_engine


def get_simulator() -> CloudEnvironmentSimulator:
    """Dependency provider for CloudEnvironmentSimulator."""
    return _simulator


def get_workflow_service() -> WorkflowService:
    """Dependency provider for WorkflowService."""
    return _workflow_service


def reset_dependencies() -> None:
    """Reset all in-memory services, seed default catalogs, and clear audit history."""
    _state_manager.reset()
    _state_manager.seed_default_services()
    _safety_engine.clear_cooldown()
    _execution_engine.clear_simulated_failures()
    _execution_engine.clear_execution_history()
    _simulator.active_scenario = ScenarioType.NORMAL
    _simulator.tick_count = 0
    _simulator.total_simulated_seconds = 0.0
    _workflow_service.clear_history()


