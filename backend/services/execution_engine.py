"""Action Execution Engine.

Executes validated infrastructure action proposals against service state,
applies resource and cost mutations, maintains state versions,
triggers safety anti-flapping cooldowns, and maintains an execution audit log.
"""

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Optional

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionResult, ExecutionStatus
from backend.schemas.safety import SafetyCheckResult
from backend.services.safety_engine import DeterministicSafetyEngine
from backend.services.state_manager import ServiceStateManager


class ActionExecutionEngine:
    """Execution engine for executing validated infrastructure action proposals.

    Features:
    - Pre-execution validation (safety approval and version freshness).
    - Pro-rated cost and utilization mutations for scaling actions.
    - Automatic state version incrementing.
    - Anti-flapping cooldown tracking via DeterministicSafetyEngine.
    - Simulated failure injection for testing verification and rollback paths.
    - In-memory execution audit log.
    """

    def __init__(
        self,
        state_manager: ServiceStateManager,
        safety_engine: Optional[DeterministicSafetyEngine] = None,
    ) -> None:
        self.state_manager = state_manager
        self.safety_engine = safety_engine
        self._lock = RLock()
        self._execution_history: List[ExecutionResult] = []
        self._simulated_failures: Dict[str, str] = {}

    def set_simulated_failure(self, service_id: str, error_code: str) -> None:
        """Inject a simulated cloud provider error for testing failure paths.

        Args:
            service_id: Specific service ID or '*' for all services.
            error_code: Error code to return (e.g., 'capacity_unavailable', 'quota_exceeded').
        """
        with self._lock:
            self._simulated_failures[service_id.strip().lower()] = error_code

    def clear_simulated_failures(self, service_id: Optional[str] = None) -> None:
        """Clear simulated failure injection."""
        with self._lock:
            if service_id is not None:
                self._simulated_failures.pop(service_id.strip().lower(), None)
            else:
                self._simulated_failures.clear()

    def get_execution_history(
        self, service_id: Optional[str] = None
    ) -> List[ExecutionResult]:
        """Retrieve execution history, optionally filtered by service ID."""
        with self._lock:
            if service_id is None:
                return [r.model_copy(deep=True) for r in self._execution_history]
            norm_id = service_id.strip().lower()
            return [
                r.model_copy(deep=True)
                for r in self._execution_history
                if r.target_service_id.lower() == norm_id
            ]

    def clear_execution_history(self) -> None:
        """Clear the in-memory execution audit log."""
        with self._lock:
            self._execution_history.clear()

    def execute(
        self,
        proposal: ActionProposal,
        safety_result: Optional[SafetyCheckResult] = None,
        enforce_freshness: bool = True,
    ) -> ExecutionResult:
        """Execute an approved infrastructure ActionProposal.

        Args:
            proposal: The ActionProposal proposed by the agent.
            safety_result: Pre-computed SafetyCheckResult. If None and a safety_engine is
                configured, it will evaluate the proposal automatically.
            enforce_freshness: If True, rejects execution if current state version has drifted.

        Returns:
            ExecutionResult containing execution status, error details, and new state version.
        """
        norm_target = proposal.target_service_id.strip().lower()

        with self._lock:
            # 1. Target service existence check
            current_state = self.state_manager.get_service(norm_target)
            if not current_state:
                result = ExecutionResult(
                    action=proposal.action,
                    target_service_id=proposal.target_service_id,
                    status=ExecutionStatus.FAILURE,
                    error_code="service_not_found",
                    error_message=f"Target service '{proposal.target_service_id}' was not found.",
                )
                self._execution_history.append(result)
                return result

            # 2. Safety evaluation resolution
            active_safety_result = safety_result
            if active_safety_result is None and self.safety_engine is not None:
                active_safety_result = self.safety_engine.evaluate(proposal, current_state)

            if active_safety_result is not None and not active_safety_result.is_approved:
                reasons = "; ".join(active_safety_result.rejection_reasons) or "Action rejected by safety engine."
                result = ExecutionResult(
                    action=proposal.action,
                    target_service_id=proposal.target_service_id,
                    status=ExecutionStatus.FAILURE,
                    error_code="safety_check_rejected",
                    error_message=f"Safety check rejected proposal: {reasons}",
                )
                self._execution_history.append(result)
                return result

            # 3. State freshness verification (anti-race condition)
            if enforce_freshness and proposal.observation_version != current_state.state_version:
                result = ExecutionResult(
                    action=proposal.action,
                    target_service_id=proposal.target_service_id,
                    status=ExecutionStatus.FAILURE,
                    error_code="stale_state_at_execution",
                    error_message=(
                        f"State version drifted between proposal ({proposal.observation_version}) "
                        f"and execution ({current_state.state_version})."
                    ),
                )
                self._execution_history.append(result)
                return result

            # 4. Simulated failure injection check
            simulated_code = self._simulated_failures.get(norm_target) or self._simulated_failures.get("*")
            if simulated_code:
                result = ExecutionResult(
                    action=proposal.action,
                    target_service_id=proposal.target_service_id,
                    status=ExecutionStatus.FAILURE,
                    error_code=simulated_code,
                    error_message=f"Simulated execution error from cloud provider: {simulated_code}",
                )
                self._execution_history.append(result)
                return result

            # 5. Apply infrastructure action mutations
            action = proposal.action
            new_version = current_state.state_version

            if action == InfrastructureAction.NO_ACTION:
                # No mutations applied
                new_version = current_state.state_version

            elif action == InfrastructureAction.SCALE_DOWN:
                target_instances = (
                    proposal.target_instances
                    if proposal.target_instances is not None
                    else max(current_state.min_instances, current_state.current_instances - 1)
                )
                curr_inst = current_state.current_instances
                cost_ratio = target_instances / curr_inst if curr_inst > 0 else 1.0
                curr_cost = current_state.cost_per_hour or 0.0
                new_cost = round(curr_cost * cost_ratio, 2)

                curr_cpu = current_state.cpu_utilization_percent or 0.0
                inverse_ratio = curr_inst / target_instances if target_instances > 0 else 1.0
                new_cpu = min(100.0, round(curr_cpu * inverse_ratio, 1))

                new_version = self.state_manager.advance_version(norm_target)
                self.state_manager.update_capacity(
                    norm_target, current_instances=target_instances, bump_version=False
                )
                self.state_manager.update_metrics(
                    norm_target,
                    cost_per_hour=new_cost,
                    cpu_utilization_percent=new_cpu,
                    bump_version=False,
                )

            elif action == InfrastructureAction.SCALE_UP:
                target_instances = (
                    proposal.target_instances
                    if proposal.target_instances is not None
                    else min(current_state.max_instances, current_state.current_instances + 1)
                )
                curr_inst = current_state.current_instances
                cost_ratio = target_instances / curr_inst if curr_inst > 0 else 1.0
                curr_cost = current_state.cost_per_hour or 0.0
                new_cost = round(curr_cost * cost_ratio, 2)

                curr_cpu = current_state.cpu_utilization_percent or 0.0
                inverse_ratio = curr_inst / target_instances if target_instances > 0 else 1.0
                new_cpu = max(0.0, round(curr_cpu * inverse_ratio, 1))

                new_version = self.state_manager.advance_version(norm_target)
                self.state_manager.update_capacity(
                    norm_target, current_instances=target_instances, bump_version=False
                )
                self.state_manager.update_metrics(
                    norm_target,
                    cost_per_hour=new_cost,
                    cpu_utilization_percent=new_cpu,
                    bump_version=False,
                )

            elif action == InfrastructureAction.STOP_IDLE_SERVICE:
                new_version = self.state_manager.advance_version(norm_target)
                self.state_manager.update_capacity(
                    norm_target, current_instances=0, min_instances=0, bump_version=False
                )
                self.state_manager.update_metrics(
                    norm_target,
                    cost_per_hour=0.0,
                    traffic_rpm=0,
                    cpu_utilization_percent=0.0,
                    memory_utilization_percent=0.0,
                    bump_version=False,
                )

            elif action == InfrastructureAction.RESIZE:
                # Downsize/rightsize instance profile: reduces hourly cost by 30%
                curr_cost = current_state.cost_per_hour or 0.0
                new_cost = round(curr_cost * 0.70, 2)
                curr_cpu = current_state.cpu_utilization_percent or 0.0
                new_cpu = min(100.0, round(curr_cpu * 1.25, 1))

                new_version = self.state_manager.advance_version(norm_target)
                self.state_manager.update_metrics(
                    norm_target,
                    cost_per_hour=new_cost,
                    cpu_utilization_percent=new_cpu,
                    bump_version=False,
                )

            elif action == InfrastructureAction.DELAY_BATCH:
                # Pause batch workload: drops traffic and CPU load
                new_version = self.state_manager.advance_version(norm_target)
                self.state_manager.update_metrics(
                    norm_target,
                    traffic_rpm=0,
                    cpu_utilization_percent=1.0,
                    bump_version=False,
                )

            # 6. Record action execution in Safety Engine for anti-flapping cooldown
            if action != InfrastructureAction.NO_ACTION and self.safety_engine is not None:
                self.safety_engine.record_action_execution(norm_target)

            result = ExecutionResult(
                action=proposal.action,
                target_service_id=proposal.target_service_id,
                status=ExecutionStatus.SUCCESS,
                error_code=None,
                error_message=None,
                new_state_version=new_version,
            )
            self._execution_history.append(result)
            return result
