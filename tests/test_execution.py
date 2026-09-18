"""Unit and integration tests for the Deterministic Execution Service."""

from datetime import datetime, timezone
import pytest

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionStatus
from backend.schemas.metrics import ServiceObservation
from backend.schemas.safety import SafetyCheckResult
from backend.schemas.workflow import DecisionResult, InvestigationResult
from backend.services.execution_service import ExecutionService
from backend.services.safety_service import SafetyEngine
from backend.services.verification_service import VerificationAgent


def create_proposal(
    action: InfrastructureAction,
    target_service_id: str = "web-service",
    version: str = "v1",
) -> ActionProposal:
    return ActionProposal(
        action=action,
        target_service_id=target_service_id,
        reason="Test execution reason",
        expected_effect="Test expected effect",
        observation_version=version,
        confidence=1.0,
    )


def approved_safety_check(version: str = "v1") -> SafetyCheckResult:
    return SafetyCheckResult(
        is_approved=True,
        proposal_version=version,
        evaluated_against_version=version,
        rejection_reasons=[],
        applied_rules=["scale_down_protection"],
    )


def rejected_safety_check(
    version: str = "v1", reason: str = "CPU utilization is too high"
) -> SafetyCheckResult:
    return SafetyCheckResult(
        is_approved=False,
        proposal_version=version,
        evaluated_against_version=version,
        rejection_reasons=[reason],
        applied_rules=["scale_down_protection"],
    )


class TestExecutionService:
    def test_successful_scale_up(self):
        service = ExecutionService()
        prop = create_proposal(InfrastructureAction.SCALE_UP, target_service_id="auth-api", version="v1")
        safe = approved_safety_check("v1")

        result = service.execute(prop, safety_check=safe)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.action == InfrastructureAction.SCALE_UP
        assert result.target_service_id == "auth-api"
        assert result.error_code is None
        assert result.error_message is None
        assert result.new_state_version == "v2"

    def test_successful_scale_down(self):
        service = ExecutionService()
        prop = create_proposal(InfrastructureAction.SCALE_DOWN, target_service_id="batch-worker", version="v3")
        safe = approved_safety_check("v3")

        result = service.execute(prop, safety_check=safe)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.action == InfrastructureAction.SCALE_DOWN
        assert result.target_service_id == "batch-worker"
        assert result.error_code is None
        assert result.new_state_version == "v4"

    def test_problem_3_capacity_unavailable_failure(self):
        """
        Problem 3: payment-api scale_up from 3 to 5 instances fails with capacity_unavailable.
        Must return status=FAILURE, error_code="capacity_unavailable", and NOT advance state version.
        """
        service = ExecutionService()
        prop = create_proposal(InfrastructureAction.SCALE_UP, target_service_id="payment-api", version="v1")
        safe = approved_safety_check("v1")

        result = service.execute(prop, safety_check=safe)
        assert result.status == ExecutionStatus.FAILURE
        assert result.action == InfrastructureAction.SCALE_UP
        assert result.target_service_id == "payment-api"
        assert result.error_code == "capacity_unavailable"
        assert result.error_message is not None
        assert result.new_state_version is None  # Must NOT advance state version on failure

    def test_rejected_unsafe_action_cannot_execute(self):
        """A proposal that failed safety checks must be rejected by the execution service."""
        service = ExecutionService()
        prop = create_proposal(InfrastructureAction.SCALE_DOWN, target_service_id="web-service", version="v1")
        safe = rejected_safety_check("v1", "Scale-down protection: CPU is 95%")

        result = service.execute(prop, safety_check=safe)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error_code == "safety_rejected"
        assert "Scale-down protection: CPU is 95%" in result.error_message
        assert result.new_state_version is None

    def test_no_action_behavior(self):
        """NO_ACTION succeeds without mutating infrastructure or advancing the state version."""
        service = ExecutionService()
        prop = create_proposal(InfrastructureAction.NO_ACTION, target_service_id="web-service", version="v2")
        safe = approved_safety_check("v2")

        result = service.execute(prop, safety_check=safe)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.action == InfrastructureAction.NO_ACTION
        assert result.target_service_id == "web-service"
        assert result.error_code is None
        assert result.new_state_version == "v2"  # Version unchanged

    def test_state_version_changes_only_after_successful_state_changing_execution(self):
        """Confirms version advances on state change, but stays unchanged on NO_ACTION and None on failure."""
        service = ExecutionService()

        # 1. Successful state-changing action advances version
        prop_up = create_proposal(InfrastructureAction.SCALE_UP, target_service_id="cache-service", version="v5")
        res_up = service.execute(prop_up, safety_check=approved_safety_check("v5"))
        assert res_up.status == ExecutionStatus.SUCCESS
        assert res_up.new_state_version == "v6"

        # 2. NO_ACTION does not advance version
        prop_no = create_proposal(InfrastructureAction.NO_ACTION, target_service_id="cache-service", version="v6")
        res_no = service.execute(prop_no, safety_check=approved_safety_check("v6"))
        assert res_no.status == ExecutionStatus.SUCCESS
        assert res_no.new_state_version == "v6"

        # 3. Failed execution produces new_state_version = None
        prop_fail = create_proposal(InfrastructureAction.SCALE_UP, target_service_id="payment-api", version="v6")
        res_fail = service.execute(prop_fail, safety_check=approved_safety_check("v6"))
        assert res_fail.status == ExecutionStatus.FAILURE
        assert res_fail.new_state_version is None

    def test_custom_failure_simulation(self):
        """Tests configurable simulated failure registration."""
        service = ExecutionService()
        service.register_failure(
            service_id="db-proxy",
            action=InfrastructureAction.RESIZE,
            error_code="quota_exceeded",
            error_message="Account quota exceeded for DB proxy resizing.",
        )

        prop = create_proposal(InfrastructureAction.RESIZE, target_service_id="db-proxy", version="v1")
        result = service.execute(prop, safety_check=approved_safety_check("v1"))

        assert result.status == ExecutionStatus.FAILURE
        assert result.error_code == "quota_exceeded"
        assert "Account quota exceeded" in result.error_message
        assert result.new_state_version is None


class TestPipelineIntegration:
    """Integration tests verifying the SafetyEngine -> ExecutionService -> VerificationAgent chain."""

    def test_problem_3_pipeline_end_to_end(self):
        """
        End-to-end integration of Problem 3:
        1. Safety approves scale_up for payment-api under load
        2. Execution fails with capacity_unavailable
        3. VerificationAgent audits failure accurately and flags workflow unsuccessful
        """
        safety_engine = SafetyEngine()
        execution_service = ExecutionService()
        verification_agent = VerificationAgent()

        obs = ServiceObservation(
            service_id="payment-api",
            cpu_utilization_percent=92.0,
            memory_utilization_percent=88.0,
            traffic_rpm=6500,
            latency_ms=420.0,
            cost_per_hour=35.0,
            observation_timestamp=datetime.now(timezone.utc),
            state_version="v1",
        )
        prop = ActionProposal(
            action=InfrastructureAction.SCALE_UP,
            target_service_id="payment-api",
            reason="Scale up due to high CPU and latency",
            expected_effect="Alleviate resource pressure",
            observation_version="v1",
            confidence=1.0,
        )
        dec = DecisionResult(
            investigation=InvestigationResult(observation=obs, identified_issues=["high CPU"], summary="High load"),
            proposal=prop,
        )

        # Step 1: Safety Check
        safety_check = safety_engine.check(prop, obs)
        assert safety_check.is_approved is True

        # Step 2: Execution
        exec_res = execution_service.execute(prop, safety_check=safety_check)
        assert exec_res.status == ExecutionStatus.FAILURE
        assert exec_res.error_code == "capacity_unavailable"
        assert exec_res.new_state_version is None

        # Step 3: Verification
        verif_res = verification_agent.verify(dec, safety_check, exec_res)
        assert verif_res.is_successful is False
        assert "capacity unavailability" in verif_res.verification_notes

    def test_successful_pipeline_end_to_end(self):
        """End-to-end integration of successful scale up across all three components."""
        safety_engine = SafetyEngine()
        execution_service = ExecutionService()
        verification_agent = VerificationAgent()

        obs = ServiceObservation(
            service_id="orders-service",
            cpu_utilization_percent=88.0,
            memory_utilization_percent=75.0,
            traffic_rpm=4500,
            latency_ms=250.0,
            cost_per_hour=25.0,
            observation_timestamp=datetime.now(timezone.utc),
            state_version="v1",
        )
        prop = ActionProposal(
            action=InfrastructureAction.SCALE_UP,
            target_service_id="orders-service",
            reason="Scale up to handle surge",
            expected_effect="Reduced latency",
            observation_version="v1",
            confidence=0.9,
        )
        dec = DecisionResult(
            investigation=InvestigationResult(observation=obs, identified_issues=[], summary="Approaching limit"),
            proposal=prop,
        )

        safety_check = safety_engine.check(prop, obs)
        assert safety_check.is_approved is True

        exec_res = execution_service.execute(prop, safety_check=safety_check)
        assert exec_res.status == ExecutionStatus.SUCCESS
        assert exec_res.new_state_version == "v2"

        post_obs = ServiceObservation(
            service_id="orders-service",
            cpu_utilization_percent=45.0,
            memory_utilization_percent=40.0,
            traffic_rpm=4500,
            latency_ms=80.0,
            cost_per_hour=35.0,
            observation_timestamp=datetime.now(timezone.utc),
            state_version="v2",
        )

        verif_res = verification_agent.verify(dec, safety_check, exec_res, post_execution_observation=post_obs)
        assert verif_res.is_successful is True
        assert "Successfully executed" in verif_res.verification_notes
        assert "confirms the state version changed to v2" in verif_res.verification_notes
