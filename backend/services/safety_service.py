"""Deterministic Safety Engine / Service for validating infrastructure action proposals."""

from datetime import datetime, timezone
from typing import List, Optional

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.metrics import ServiceObservation
from backend.schemas.safety import SafetyCheckResult


class SafetyService:
    """
    Deterministic Safety Service that evaluates ActionProposals against current ServiceObservations.
    Ensures that no unsafe, stale, or conflicting infrastructure actions are executed.
    """

    def __init__(self, staleness_threshold_seconds: int = 300):
        self.staleness_threshold_seconds = staleness_threshold_seconds

    def check(
        self,
        proposal: ActionProposal,
        observation: ServiceObservation,
        now: Optional[datetime] = None,
    ) -> SafetyCheckResult:
        """
        Evaluates an ActionProposal against a ServiceObservation deterministically.

        Args:
            proposal: The proposed infrastructure action.
            observation: The latest service metrics observation.
            now: Optional override for current time (useful for deterministic testing).

        Returns:
            SafetyCheckResult indicating approval status, applied rules, and any rejection reasons.
        """
        rejection_reasons: List[str] = []
        applied_rules: List[str] = [
            "service_target_safety",
            "observation_version_safety",
            "staleness_safety",
            "scale_down_protection",
            "stop_idle_protection",
        ]

        # 1. Target Service Safety
        if proposal.target_service_id != observation.service_id:
            rejection_reasons.append(
                f"Target service mismatch: proposal targets '{proposal.target_service_id}' "
                f"but observation is for '{observation.service_id}'."
            )

        # 2. Observation Version Safety (prevents acting on stale / desynchronized state)
        if proposal.observation_version != observation.state_version:
            rejection_reasons.append(
                "Observation version mismatch: proposal is based on a different state version."
            )

        # 3. Staleness Safety (rejects active actions on outdated observation metrics)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        obs_time = observation.observation_timestamp
        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)

        time_diff = (current_time - obs_time).total_seconds()
        is_stale = time_diff > self.staleness_threshold_seconds

        if is_stale and proposal.action != InfrastructureAction.NO_ACTION:
            rejection_reasons.append(
                f"Stale observation safety: observation is {time_diff:.1f}s old, "
                f"which exceeds the threshold of {self.staleness_threshold_seconds}s."
            )

        # 4. Scale-Down Protection (blocks scale down if utilization or latency is elevated)
        if proposal.action == InfrastructureAction.SCALE_DOWN:
            cpu = observation.cpu_utilization_percent
            mem = observation.memory_utilization_percent
            traffic = observation.traffic_rpm
            latency = observation.latency_ms

            if cpu > 80.0:
                rejection_reasons.append(f"Scale-down protection: CPU utilization is high ({cpu}% > 80%).")
            if mem > 80.0:
                rejection_reasons.append(f"Scale-down protection: Memory utilization is high ({mem}% > 80%).")
            if traffic > 5000:
                rejection_reasons.append(f"Scale-down protection: Traffic RPM is high ({traffic} > 5000).")
            if latency > 300.0:
                rejection_reasons.append(f"Scale-down protection: Latency is high ({latency}ms > 300ms).")

        # 5. Stop-Idle Protection (ensures service is truly idle before shutting down)
        if proposal.action == InfrastructureAction.STOP_IDLE_SERVICE:
            traffic = observation.traffic_rpm
            cpu = observation.cpu_utilization_percent
            mem = observation.memory_utilization_percent

            if traffic > 0:
                rejection_reasons.append(
                    f"Stop idle service protection: Traffic RPM is greater than 0 ({traffic} RPM)."
                )
            if cpu > 5.0 or mem > 10.0:
                rejection_reasons.append(
                    f"Stop idle service protection: Service is not idle (CPU: {cpu}%, Mem: {mem}%)."
                )

        is_approved = len(rejection_reasons) == 0

        return SafetyCheckResult(
            is_approved=is_approved,
            proposal_version=proposal.observation_version,
            evaluated_against_version=observation.state_version,
            rejection_reasons=rejection_reasons,
            applied_rules=applied_rules,
        )


# Alias for compatibility with agent-based naming
SafetyEngine = SafetyService
