"""Deterministic Verification Agent / Service for validating workflow execution outcomes."""

from typing import Optional

from backend.schemas.actions import InfrastructureAction
from backend.schemas.execution import ExecutionResult, ExecutionStatus
from backend.schemas.metrics import ServiceObservation
from backend.schemas.safety import SafetyCheckResult
from backend.schemas.workflow import DecisionResult, VerificationResult


class VerificationService:
    """
    Deterministic Verification Service that validates the complete workflow outcome:
    evaluates decisions, safety checks, execution results, and post-execution observations.
    Operates 100% deterministically without external LLM dependencies.
    """

    def verify(
        self,
        decision: DecisionResult,
        safety_check: SafetyCheckResult,
        execution: Optional[ExecutionResult] = None,
        post_execution_observation: Optional[ServiceObservation] = None,
    ) -> VerificationResult:
        """
        Verifies workflow execution against safety and operational criteria.

        Args:
            decision: The decision containing the action proposal.
            safety_check: The result from the SafetyEngine evaluation.
            execution: The execution result if an action was executed.
            post_execution_observation: The new metrics observation after execution.

        Returns:
            VerificationResult with is_successful flag and explanatory notes.
        """
        notes_parts = []
        is_successful = False

        action = decision.proposal.action
        target_service = decision.proposal.target_service_id

        # 1. Safety Rejection: Safety Engine rejected the proposal
        if not safety_check.is_approved:
            notes_parts.append("Execution was not authorized by the Safety Engine.")
            notes_parts.append("No infrastructure changes were made.")
            is_successful = False

        # 2. No Action: Proposal was NO_ACTION and safely approved
        elif action == InfrastructureAction.NO_ACTION:
            notes_parts.append(f"Action '{action.value}' was approved by the Safety Engine.")
            notes_parts.append("No infrastructure change was required.")
            is_successful = True

        # 3. Active Action (SCALE_UP, SCALE_DOWN, RESIZE, STOP_IDLE_SERVICE, DELAY_BATCH)
        else:
            if execution is None:
                notes_parts.append(
                    "Execution result is missing despite safety approval for an active infrastructure change."
                )
                is_successful = False
            else:
                # 3a. Execution Failure
                if execution.status == ExecutionStatus.FAILURE:
                    is_successful = False
                    if execution.error_code == "capacity_unavailable":
                        notes_parts.append(
                            f"Execution failed due to capacity unavailability for action '{action.value}' "
                            f"on service '{target_service}'."
                        )
                    else:
                        err_code = execution.error_code or "Unknown"
                        err_msg = execution.error_message or "No message provided."
                        notes_parts.append(
                            f"Execution failed (Code: {err_code}, Message: {err_msg}) "
                            f"for action '{action.value}' on service '{target_service}'."
                        )

                # 3b. Execution Success
                elif execution.status == ExecutionStatus.SUCCESS:
                    is_successful = True
                    notes_parts.append(
                        f"Successfully executed action '{action.value}' on target service '{target_service}'."
                    )
                    if execution.new_state_version:
                        notes_parts.append(
                            f"New state version reported by execution layer: {execution.new_state_version}."
                        )

        # 4. Post-Execution Observation Validation
        if post_execution_observation:
            observed_version = post_execution_observation.state_version
            if execution and execution.new_state_version:
                if observed_version == execution.new_state_version:
                    notes_parts.append(
                        f"Post-execution observation confirms the state version changed to {observed_version}."
                    )
                else:
                    notes_parts.append(
                        f"Post-execution observation state version ({observed_version}) does not match "
                        f"expected new version ({execution.new_state_version})."
                    )
            else:
                notes_parts.append(
                    f"Post-execution observation recorded with state version: {observed_version}."
                )

        return VerificationResult(
            decision=decision,
            safety_check=safety_check,
            execution=execution,
            is_successful=is_successful,
            verification_notes=" ".join(notes_parts),
        )


# Alias for compatibility with agent-based naming
VerificationAgent = VerificationService
