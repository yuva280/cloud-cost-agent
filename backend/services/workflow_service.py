"""Workflow Service.

Connects the AI Agent Orchestrator with live infrastructure telemetry from
ServiceStateManager and action mutations from ActionExecutionEngine.
Manages workflow execution, report history persistence, and cost savings analytics.
"""

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.orchestrator.orchestrator import Orchestrator
from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionResult, ExecutionStatus
from backend.schemas.metrics import ServiceObservation
from backend.schemas.service_state import ServiceState
from backend.schemas.workflow import WorkflowReport
from backend.services.execution_engine import ActionExecutionEngine
from backend.services.safety_engine import DeterministicSafetyEngine
from backend.services.state_manager import ServiceNotFoundError, ServiceStateManager


class CostSavingsSummary(BaseModel):
    """Cumulative cost savings and optimization metrics."""

    total_workflows_run: int = Field(default=0, description="Total workflows executed.")
    successful_optimizations: int = Field(default=0, description="Workflows resulting in successful cost-reducing actions.")
    failed_executions: int = Field(default=0, description="Workflows where execution failed.")
    prevented_unsafe_actions: int = Field(default=0, description="Workflows where safety engine blocked action.")
    total_hourly_savings: float = Field(default=0.0, description="Total hourly spend reduction in $/hr.")
    projected_monthly_savings: float = Field(default=0.0, description="Projected monthly spend reduction (730 hrs).")
    projected_annual_savings: float = Field(default=0.0, description="Projected annual spend reduction (8760 hrs).")
    optimized_services: List[str] = Field(default_factory=list, description="List of services with applied cost optimizations.")
    per_service_savings: Dict[str, float] = Field(default_factory=dict, description="Hourly savings mapped by service ID.")


class WorkflowService:
    """Service managing autonomous agent workflows, reports, and cost analytics."""

    def __init__(
        self,
        state_manager: ServiceStateManager,
        execution_engine: ActionExecutionEngine,
        safety_engine: Optional[DeterministicSafetyEngine] = None,
        orchestrator: Optional[Orchestrator] = None,
    ) -> None:
        self.state_manager = state_manager
        self.execution_engine = execution_engine
        self.safety_engine = safety_engine
        self._lock = RLock()
        self._reports: Dict[str, WorkflowReport] = {}
        self._service_savings: Dict[str, float] = {}

        # Default simulated failure for Problem 3 compatibility: payment-api scale_up -> capacity_unavailable
        self.execution_engine.set_simulated_failure("payment-api", "capacity_unavailable")

        # Instantiate Orchestrator with wired observer and executor callbacks
        self.orchestrator = orchestrator or Orchestrator(
            executor=self._execute_action,
            observer=self._observe_service,
        )

    def _observe_service(self, service_id: str) -> ServiceObservation:
        """Observer callback for the orchestrator to fetch fresh post-execution telemetry."""
        return self.state_manager.create_observation(service_id)

    def _execute_action(self, proposal: ActionProposal) -> ExecutionResult:
        """Executor callback for the orchestrator to apply live infrastructure mutations."""
        return self.execution_engine.execute(proposal, enforce_freshness=True)

    def run_workflow(
        self,
        service_id: Optional[str] = None,
        observation: Optional[ServiceObservation] = None,
    ) -> WorkflowReport:
        """Execute the end-to-end agent workflow for a service or explicit observation.

        Args:
            service_id: Target service ID to observe and optimize.
            observation: Explicit observation to analyze (used if service_id not supplied).

        Returns:
            WorkflowReport containing initial observation, investigation, decision,
            safety check, execution result, and final verification notes.

        Raises:
            ValueError: If neither service_id nor observation is provided.
            ServiceNotFoundError: If service_id does not exist in state manager.
        """
        if observation is None:
            if not service_id:
                raise ValueError("Either 'service_id' or 'observation' must be provided.")
            obs = self.state_manager.create_observation(service_id)
        else:
            obs = observation
            # Auto-register in state manager if not present, so execution and observer can track it
            norm_id = obs.service_id.strip().lower()
            if not self.state_manager.get_service(norm_id):
                self.state_manager.register_service(
                    ServiceState.from_observation(
                        obs,
                        current_instances=3,
                        min_instances=1,
                        max_instances=10,
                        max_latency_ms=max(200.0, obs.latency_ms * 1.5),
                        healthy=True,
                    )
                )

        initial_cost = obs.cost_per_hour
        target_id = obs.service_id.strip().lower()

        # Execute full agent pipeline through orchestrator
        report = self.orchestrator.run(obs)

        with self._lock:
            # Store workflow report
            self._reports[report.workflow_id] = report

            # Track cost savings if execution succeeded for cost-reducing actions
            verification = report.final_verification
            if verification.is_successful and verification.execution:
                if verification.execution.status == ExecutionStatus.SUCCESS:
                    action = verification.decision.proposal.action
                    if action in (
                        InfrastructureAction.SCALE_DOWN,
                        InfrastructureAction.STOP_IDLE_SERVICE,
                        InfrastructureAction.RESIZE,
                    ):
                        new_state = self.state_manager.get_service(target_id)
                        new_cost = new_state.cost_per_hour if new_state and new_state.cost_per_hour is not None else 0.0
                        hourly_saved = max(0.0, round(initial_cost - new_cost, 2))
                        if hourly_saved > 0:
                            current_saved = self._service_savings.get(target_id, 0.0)
                            self._service_savings[target_id] = round(current_saved + hourly_saved, 2)

            return report

    def list_reports(
        self, service_id: Optional[str] = None, limit: int = 50
    ) -> List[WorkflowReport]:
        """Retrieve stored workflow reports, optionally filtered by service ID."""
        with self._lock:
            all_reports = list(self._reports.values())
            if service_id:
                norm_id = service_id.strip().lower()
                all_reports = [
                    r for r in all_reports
                    if r.initial_observation.service_id.lower() == norm_id
                ]
            # Return newest first up to limit
            return list(reversed(all_reports))[:limit]

    def get_report(self, workflow_id: str) -> Optional[WorkflowReport]:
        """Retrieve a single workflow report by its ID."""
        with self._lock:
            return self._reports.get(workflow_id)

    def get_savings_summary(self) -> CostSavingsSummary:
        """Calculate aggregate cost savings and workflow outcome analytics."""
        with self._lock:
            total_runs = len(self._reports)
            successful_opts = 0
            failed_execs = 0
            prevented_unsafe = 0

            for r in self._reports.values():
                v = r.final_verification
                if not v.safety_check.is_approved:
                    prevented_unsafe += 1
                elif v.execution and v.execution.status == ExecutionStatus.FAILURE:
                    failed_execs += 1
                elif v.execution and v.execution.status == ExecutionStatus.SUCCESS:
                    if v.decision.proposal.action in (
                        InfrastructureAction.SCALE_DOWN,
                        InfrastructureAction.STOP_IDLE_SERVICE,
                        InfrastructureAction.RESIZE,
                    ):
                        successful_opts += 1

            total_hourly = sum(self._service_savings.values())
            monthly = round(total_hourly * 730.0, 2)
            annual = round(total_hourly * 8760.0, 2)

            return CostSavingsSummary(
                total_workflows_run=total_runs,
                successful_optimizations=successful_opts,
                failed_executions=failed_execs,
                prevented_unsafe_actions=prevented_unsafe,
                total_hourly_savings=round(total_hourly, 2),
                projected_monthly_savings=monthly,
                projected_annual_savings=annual,
                optimized_services=list(self._service_savings.keys()),
                per_service_savings=self._service_savings.copy(),
            )

    def clear_history(self) -> None:
        """Clear stored workflow reports and savings metrics."""
        with self._lock:
            self._reports.clear()
            self._service_savings.clear()
            self.execution_engine.set_simulated_failure("payment-api", "capacity_unavailable")
