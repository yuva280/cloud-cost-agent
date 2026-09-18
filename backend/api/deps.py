"""FastAPI dependency injection providers for backend singletons."""

from typing import Tuple

from backend.services.execution_engine import ActionExecutionEngine
from backend.services.safety_engine import DeterministicSafetyEngine
from backend.services.state_manager import ServiceStateManager

# Module-level singletons
_state_manager = ServiceStateManager()
_safety_engine = DeterministicSafetyEngine()
_execution_engine = ActionExecutionEngine(
    state_manager=_state_manager, safety_engine=_safety_engine
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


def reset_dependencies() -> None:
    """Reset all in-memory services, seed default catalogs, and clear audit history."""
    _state_manager.reset()
    _state_manager.seed_default_services()
    _safety_engine.clear_cooldown()
    _execution_engine.clear_simulated_failures()
    _execution_engine.clear_execution_history()
