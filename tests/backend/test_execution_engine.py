"""Unit tests for ActionExecutionEngine."""

from datetime import datetime, timezone
import pytest

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionResult, ExecutionStatus
from backend.schemas.safety import SafetyCheckResult
from backend.schemas.service_state import ServiceState
from backend.services.execution_engine import ActionExecutionEngine
from backend.services.safety_engine import DeterministicSafetyEngine
from backend.services.state_manager import ServiceStateManager


@pytest.fixture
def state_manager() -> ServiceStateManager:
    manager = ServiceStateManager()
    manager.reset()
    manager.seed_default_services()
    return manager


@pytest.fixture
def safety_engine() -> DeterministicSafetyEngine:
    return DeterministicSafetyEngine()


@pytest.fixture
def execution_engine(
    state_manager: ServiceStateManager, safety_engine: DeterministicSafetyEngine
) -> ActionExecutionEngine:
    return ActionExecutionEngine(state_manager=state_manager, safety_engine=safety_engine)


# ---------------------------------------------------------------------------
# Successful Execution Tests
# ---------------------------------------------------------------------------

def test_execute_scale_down_success(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
    safety_engine: DeterministicSafetyEngine,
) -> None:
    # cart-service starts with 4 instances, $1.60/hr, 12% CPU, version v1
    initial = state_manager.get_service_or_raise("cart-service")
    assert initial.current_instances == 4
    assert initial.state_version == "v1"

    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Underutilized web traffic during off-peak hours.",
        expected_effect="Scale down from 4 to 2 instances to reduce cost.",
        observation_version="v1",
        confidence=0.92,
        target_instances=2,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=["R11_CAPACITY_FLOOR"],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.action == InfrastructureAction.SCALE_DOWN
    assert result.target_service_id == "cart-service"
    assert result.new_state_version == "v2"
    assert result.error_code is None

    # Check state mutations
    updated = state_manager.get_service_or_raise("cart-service")
    assert updated.current_instances == 2
    assert updated.cost_per_hour == 0.80  # 1.60 * (2/4)
    assert updated.cpu_utilization_percent == 24.0  # 12.0 * (4/2)
    assert updated.state_version == "v2"

    # Cooldown should now be recorded
    is_cooling, _ = safety_engine.is_in_cooldown("cart-service", datetime.now(timezone.utc))
    assert is_cooling is True


def test_execute_scale_up_success(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_UP,
        target_service_id="cart-service",
        reason="Anticipating flash sale traffic.",
        expected_effect="Scale up from 4 to 6 instances.",
        observation_version="v1",
        confidence=0.95,
        target_instances=6,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.new_state_version == "v2"

    updated = state_manager.get_service_or_raise("cart-service")
    assert updated.current_instances == 6
    assert updated.cost_per_hour == 2.40  # 1.60 * (6/4)
    assert updated.cpu_utilization_percent == 8.0  # 12.0 * (4/6) = 8.0


def test_execute_stop_idle_service(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    # analytics-worker starts with 2 instances, $0.80/hr, 0 traffic
    proposal = ActionProposal(
        action=InfrastructureAction.STOP_IDLE_SERVICE,
        target_service_id="analytics-worker",
        reason="Idle batch worker with zero queue traffic.",
        expected_effect="Stop idle service to reduce costs to 0.",
        observation_version="v1",
        confidence=0.98,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.new_state_version == "v2"

    updated = state_manager.get_service_or_raise("analytics-worker")
    assert updated.current_instances == 0
    assert updated.cost_per_hour == 0.0
    assert updated.traffic_rpm == 0
    assert updated.cpu_utilization_percent == 0.0
    assert updated.memory_utilization_percent == 0.0


def test_execute_resize(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    # cart-service starts at $1.60
    proposal = ActionProposal(
        action=InfrastructureAction.RESIZE,
        target_service_id="cart-service",
        reason="Downsize CPU instance type profile.",
        expected_effect="Rightsize resources.",
        observation_version="v1",
        confidence=0.88,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.SUCCESS
    updated = state_manager.get_service_or_raise("cart-service")
    assert updated.cost_per_hour == 1.12  # 1.60 * 0.70
    assert updated.cpu_utilization_percent == 15.0  # 12.0 * 1.25


def test_execute_delay_batch(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    proposal = ActionProposal(
        action=InfrastructureAction.DELAY_BATCH,
        target_service_id="analytics-worker",
        reason="Shift processing window.",
        expected_effect="Delay batch until off-peak window.",
        observation_version="v1",
        confidence=0.85,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.SUCCESS
    updated = state_manager.get_service_or_raise("analytics-worker")
    assert updated.traffic_rpm == 0
    assert updated.cpu_utilization_percent == 1.0


def test_execute_no_action(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
    safety_engine: DeterministicSafetyEngine,
) -> None:
    proposal = ActionProposal(
        action=InfrastructureAction.NO_ACTION,
        target_service_id="cart-service",
        reason="Metrics are healthy and optimal.",
        expected_effect="Maintain current capacity.",
        observation_version="v1",
        confidence=1.0,
    )

    result = execution_engine.execute(proposal)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.new_state_version == "v1"

    # NO_ACTION must not record a cooldown
    is_cooling, _ = safety_engine.is_in_cooldown("cart-service", datetime.now(timezone.utc))
    assert is_cooling is False


# ---------------------------------------------------------------------------
# Validation & Error Handling Tests
# ---------------------------------------------------------------------------

def test_execute_rejected_by_safety(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Underutilized.",
        expected_effect="Scale down.",
        observation_version="v1",
        confidence=0.90,
    )

    safety_result = SafetyCheckResult(
        is_approved=False,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=["Service is currently in cooldown window."],
        applied_rules=["R13_ACTION_COOLDOWN"],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.FAILURE
    assert result.error_code == "safety_check_rejected"
    assert "cooldown window" in (result.error_message or "")

    # State must be unaltered
    state = state_manager.get_service_or_raise("cart-service")
    assert state.state_version == "v1"
    assert state.current_instances == 4


def test_execute_auto_evaluates_with_safety_engine(
    execution_engine: ActionExecutionEngine,
) -> None:
    # Low confidence proposal should be rejected by safety engine automatically
    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Maybe scale down?",
        expected_effect="Scale down.",
        observation_version="v1",
        confidence=0.40,  # Below 0.70 minimum
    )

    result = execution_engine.execute(proposal, safety_result=None)

    assert result.status == ExecutionStatus.FAILURE
    assert result.error_code == "safety_check_rejected"
    assert "confidence" in (result.error_message or "").lower()


def test_execute_stale_state_version_drift(
    execution_engine: ActionExecutionEngine,
    state_manager: ServiceStateManager,
) -> None:
    # Drift live state version to v2 before execution
    state_manager.advance_version("cart-service")
    live_state = state_manager.get_service_or_raise("cart-service")
    assert live_state.state_version == "v2"

    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Based on older v1 telemetry.",
        expected_effect="Scale down.",
        observation_version="v1",  # Stale!
        confidence=0.90,
        target_instances=2,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.FAILURE
    assert result.error_code == "stale_state_at_execution"
    assert "drifted" in (result.error_message or "")


def test_execute_service_not_found(execution_engine: ActionExecutionEngine) -> None:
    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="non-existent-service",
        reason="Unknown service.",
        expected_effect="Scale down.",
        observation_version="v1",
        confidence=0.90,
    )

    result = execution_engine.execute(proposal)

    assert result.status == ExecutionStatus.FAILURE
    assert result.error_code == "service_not_found"


def test_simulated_failure_injection(execution_engine: ActionExecutionEngine) -> None:
    execution_engine.set_simulated_failure("cart-service", "capacity_unavailable")

    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_UP,
        target_service_id="cart-service",
        reason="Scale up.",
        expected_effect="Scale up.",
        observation_version="v1",
        confidence=0.95,
        target_instances=6,
    )

    safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    result = execution_engine.execute(proposal, safety_result=safety_result)

    assert result.status == ExecutionStatus.FAILURE
    assert result.error_code == "capacity_unavailable"

    # Clear and retry
    execution_engine.clear_simulated_failures("cart-service")
    retry_result = execution_engine.execute(proposal, safety_result=safety_result)
    assert retry_result.status == ExecutionStatus.SUCCESS


def test_execution_history_tracking(execution_engine: ActionExecutionEngine) -> None:
    execution_engine.clear_execution_history()
    assert len(execution_engine.get_execution_history()) == 0

    proposal1 = ActionProposal(
        action=InfrastructureAction.NO_ACTION,
        target_service_id="cart-service",
        reason="Healthy.",
        expected_effect="None.",
        observation_version="v1",
        confidence=1.0,
    )
    proposal2 = ActionProposal(
        action=InfrastructureAction.NO_ACTION,
        target_service_id="auth-service",
        reason="Healthy.",
        expected_effect="None.",
        observation_version="v1",
        confidence=1.0,
    )

    execution_engine.execute(proposal1)
    execution_engine.execute(proposal2)

    history = execution_engine.get_execution_history()
    assert len(history) == 2

    cart_history = execution_engine.get_execution_history(service_id="cart-service")
    assert len(cart_history) == 1
    assert cart_history[0].target_service_id == "cart-service"
