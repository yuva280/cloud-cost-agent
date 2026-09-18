"""End-to-End Integration Tests for Backend Core Services.

Tests complete interactions between:
- ServiceStateManager
- DeterministicSafetyEngine
- ActionExecutionEngine
- Pydantic Schemas (ServiceState, ServiceObservation, ActionProposal, SafetyCheckResult, ExecutionResult)
"""

from datetime import datetime, timezone
import pytest

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionStatus
from backend.schemas.safety import SafetyCheckResult
from backend.services.execution_engine import ActionExecutionEngine
from backend.services.safety_engine import DeterministicSafetyEngine, EmergencyPolicy, SafetyContext
from backend.services.state_manager import ServiceStateManager


@pytest.fixture
def integrated_system():
    state_manager = ServiceStateManager()
    state_manager.reset()
    state_manager.seed_default_services()

    safety_engine = DeterministicSafetyEngine()
    execution_engine = ActionExecutionEngine(state_manager=state_manager, safety_engine=safety_engine)

    return state_manager, safety_engine, execution_engine


def test_e2e_successful_scale_down_and_anti_flapping(integrated_system):
    state_mgr, safety_eng, exec_eng = integrated_system

    # Step 1: Telemetry snapshot for cart-service
    obs = state_mgr.create_observation("cart-service")
    assert obs.service_id == "cart-service"
    assert obs.state_version == "v1"
    assert obs.cost_per_hour == 1.60

    # Step 2: Agent proposes SCALE_DOWN from 4 to 2 instances
    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Consistent low traffic and underutilized CPU.",
        expected_effect="Scale down to save cloud spend.",
        observation_version=obs.state_version,
        confidence=0.92,
        target_instances=2,
    )

    # Step 3: Evaluate proposal with Deterministic Safety Engine
    current_state = state_mgr.get_service_or_raise("cart-service")
    safety_result = safety_eng.evaluate(proposal, current_state)
    assert safety_result.is_approved is True
    assert len(safety_result.rejection_reasons) == 0

    # Step 4: Execute proposal via Action Execution Engine
    exec_result = exec_eng.execute(proposal, safety_result=safety_result)
    assert exec_result.status == ExecutionStatus.SUCCESS
    assert exec_result.new_state_version == "v2"

    # Step 5: Verify post-execution live state
    new_state = state_mgr.get_service_or_raise("cart-service")
    assert new_state.current_instances == 2
    assert new_state.cost_per_hour == 0.80
    assert new_state.state_version == "v2"

    # Step 6: Anti-flapping test - Immediate subsequent proposal must be rejected by Safety Engine
    new_obs = state_mgr.create_observation("cart-service")
    rapid_proposal = ActionProposal(
        action=InfrastructureAction.SCALE_UP,
        target_service_id="cart-service",
        reason="Rapid change.",
        expected_effect="Scale up again.",
        observation_version=new_obs.state_version,
        confidence=0.95,
        target_instances=4,
    )
    rapid_safety = safety_eng.evaluate(rapid_proposal, new_state)
    assert rapid_safety.is_approved is False
    assert any("cooldown" in r.lower() for r in rapid_safety.rejection_reasons)

    # Execution engine must reject if safety check fails
    exec_rapid = exec_eng.execute(rapid_proposal, safety_result=rapid_safety)
    assert exec_rapid.status == ExecutionStatus.FAILURE
    assert exec_rapid.error_code == "safety_check_rejected"


def test_e2e_protected_service_rejection(integrated_system):
    state_mgr, safety_eng, exec_eng = integrated_system

    # Attempt to stop production database
    obs = state_mgr.create_observation("prod-db")
    proposal = ActionProposal(
        action=InfrastructureAction.STOP_IDLE_SERVICE,
        target_service_id="prod-db",
        reason="Idle query traffic during maintenance.",
        expected_effect="Stop database.",
        observation_version=obs.state_version,
        confidence=0.99,
    )

    current_state = state_mgr.get_service_or_raise("prod-db")
    safety_result = safety_eng.evaluate(proposal, current_state)

    assert safety_result.is_approved is False
    assert any("protected" in r.lower() for r in safety_result.rejection_reasons)

    exec_result = exec_eng.execute(proposal, safety_result=safety_result)
    assert exec_result.status == ExecutionStatus.FAILURE
    assert exec_result.error_code == "safety_check_rejected"

    # Verify prod-db remains untouched
    assert state_mgr.get_service_or_raise("prod-db").current_instances == 2


def test_e2e_stale_state_race_condition(integrated_system):
    state_mgr, safety_eng, exec_eng = integrated_system

    # Observation at v1
    obs = state_mgr.create_observation("cart-service")
    assert obs.state_version == "v1"

    proposal = ActionProposal(
        action=InfrastructureAction.SCALE_DOWN,
        target_service_id="cart-service",
        reason="Scale down.",
        expected_effect="Reduce instances.",
        observation_version=obs.state_version,  # v1
        confidence=0.90,
        target_instances=2,
    )

    # Pre-evaluated safety check approved at v1
    stale_safety_result = SafetyCheckResult(
        is_approved=True,
        proposal_version="v1",
        evaluated_against_version="v1",
        rejection_reasons=[],
        applied_rules=[],
    )

    # State version advances in background to v2 before execution
    state_mgr.advance_version("cart-service")
    assert state_mgr.get_service_or_raise("cart-service").state_version == "v2"

    # Execution Engine catches the version drift
    exec_result = exec_eng.execute(proposal, safety_result=stale_safety_result)
    assert exec_result.status == ExecutionStatus.FAILURE
    assert exec_result.error_code == "stale_state_at_execution"
