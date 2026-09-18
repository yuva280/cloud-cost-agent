"""Deterministic Execution Service for executing and simulating approved infrastructure actions."""

import re
from typing import Dict, Optional, Tuple

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionResult, ExecutionStatus
from backend.schemas.safety import SafetyCheckResult


class ExecutionService:
    """
    Deterministic Execution Service that simulates execution of approved ActionProposals.
    Enforces safety validation before execution, advances state versions on state changes,
    and supports deterministic simulation of infrastructure failure modes.
    """

    def __init__(
        self,
        simulated_failures: Optional[Dict[Tuple[str, InfrastructureAction], Tuple[str, str]]] = None,
    ):
        """
        Args:
            simulated_failures: Optional mapping of (service_id, action) to (error_code, error_message).
        """
        # Built-in or injected failure scenarios
        self._simulated_failures: Dict[Tuple[str, InfrastructureAction], Tuple[str, str]] = (
            simulated_failures.copy() if simulated_failures else {}
        )

        # Explicit support for Problem 3 failure scenario:
        # payment-api scale_up from 3 to 5 instances -> capacity_unavailable
        if ("payment-api", InfrastructureAction.SCALE_UP) not in self._simulated_failures:
            self._simulated_failures[("payment-api", InfrastructureAction.SCALE_UP)] = (
                "capacity_unavailable",
                "Host cluster capacity unavailable for payment-api scale_up from 3 to 5 instances.",
            )

    def register_failure(
        self,
        service_id: str,
        action: InfrastructureAction,
        error_code: str,
        error_message: str,
    ) -> None:
        """Registers a deterministic simulated failure for a specific service and action."""
        self._simulated_failures[(service_id, action)] = (error_code, error_message)

    def clear_failures(self) -> None:
        """Clears all configured simulated failures."""
        self._simulated_failures.clear()

    def _advance_state_version(self, current_version: str) -> str:
        """
        Deterministically advances a state version string (e.g. 'v1' -> 'v2', '1' -> '2').
        """
        match = re.match(r"^([a-zA-Z_-]+)(\d+)$", current_version)
        if match:
            prefix, num = match.groups()
            return f"{prefix}{int(num) + 1}"

        if current_version.isdigit():
            return str(int(current_version) + 1)

        return f"{current_version}_v2"

    def execute(
        self,
        proposal: ActionProposal,
        safety_check: Optional[SafetyCheckResult] = None,
        simulate_failure: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Executes or simulates the execution of an ActionProposal.

        Args:
            proposal: The action proposal to execute.
            safety_check: The result of safety validation. If provided and not approved,
                          execution will be rejected.
            simulate_failure: Optional error code to trigger a forced failure for testing.

        Returns:
            ExecutionResult documenting the status, error details (if any), and new state version.
        """
        # 1. Reject execution if safety check failed
        if safety_check is not None and not safety_check.is_approved:
            reasons = "; ".join(safety_check.rejection_reasons) or "Proposal was rejected by Safety Engine."
            return ExecutionResult(
                action=proposal.action,
                target_service_id=proposal.target_service_id,
                status=ExecutionStatus.FAILURE,
                error_code="safety_rejected",
                error_message=f"Execution rejected by Safety Engine: {reasons}",
                new_state_version=None,
            )

        # 2. Handle NO_ACTION (successful, but no infrastructure mutation or version advance)
        if proposal.action == InfrastructureAction.NO_ACTION:
            return ExecutionResult(
                action=proposal.action,
                target_service_id=proposal.target_service_id,
                status=ExecutionStatus.SUCCESS,
                error_code=None,
                error_message=None,
                new_state_version=proposal.observation_version,
            )

        # 3. Check for deterministic failure simulation
        # Priority: explicit argument > configured mapping
        failure_key = (proposal.target_service_id, proposal.action)
        if simulate_failure:
            return ExecutionResult(
                action=proposal.action,
                target_service_id=proposal.target_service_id,
                status=ExecutionStatus.FAILURE,
                error_code=simulate_failure,
                error_message=f"Simulated execution failure: {simulate_failure}",
                new_state_version=None,
            )

        if failure_key in self._simulated_failures:
            err_code, err_msg = self._simulated_failures[failure_key]
            return ExecutionResult(
                action=proposal.action,
                target_service_id=proposal.target_service_id,
                status=ExecutionStatus.FAILURE,
                error_code=err_code,
                error_message=err_msg,
                new_state_version=None,
            )

        # 4. Successful execution of state-changing infrastructure action
        new_version = self._advance_state_version(proposal.observation_version)
        return ExecutionResult(
            action=proposal.action,
            target_service_id=proposal.target_service_id,
            status=ExecutionStatus.SUCCESS,
            error_code=None,
            error_message=None,
            new_state_version=new_version,
        )
