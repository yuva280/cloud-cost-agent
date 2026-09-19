from datetime import datetime, timezone
from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.metrics import ServiceObservation
from backend.schemas.safety import SafetyCheckResult


class SafetyEngine:
    """
    Deterministic Safety Engine that evaluates ActionProposals against
    current ServiceObservations.

    It does not execute actions.
    """

    def __init__(self, staleness_threshold_seconds: int = 300):
        self.staleness_threshold_seconds = staleness_threshold_seconds

    def check(
        self,
        proposal: ActionProposal,
        observation: ServiceObservation
    ) -> SafetyCheckResult:

        rejection_reasons = []

        applied_rules = [
            "observation_version_safety",
            "future_timestamp_safety",
            "staleness_safety",
            "scale_down_protection",
        ]

        # 1. Observation Version Safety
        if proposal.observation_version != observation.state_version:
            rejection_reasons.append(
                "Observation version mismatch: proposal is based on "
                "a different state version."
            )

        # 2. Timestamp Safety
        now = datetime.now(timezone.utc)

        obs_time = observation.observation_timestamp

        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)

        time_diff = (now - obs_time).total_seconds()

        # 2a. Future Timestamp Safety
        if time_diff < 0 and proposal.action != InfrastructureAction.NO_ACTION:
            rejection_reasons.append(
                "Future timestamp safety: observation timestamp is "
                f"{abs(time_diff):.1f}s ahead of current time."
            )

        # 2b. Stale Observation Safety
        is_stale = time_diff > self.staleness_threshold_seconds

        if is_stale and proposal.action != InfrastructureAction.NO_ACTION:
            rejection_reasons.append(
                "Stale observation safety: observation is "
                f"{time_diff:.1f}s old, which exceeds the threshold."
            )

        # 3. Scale-Down Protection
        if proposal.action == InfrastructureAction.SCALE_DOWN:
            cpu = observation.cpu_utilization_percent
            mem = observation.memory_utilization_percent
            traffic = observation.traffic_rpm
            latency = observation.latency_ms

            if cpu > 80.0:
                rejection_reasons.append(
                    f"Scale-down protection: CPU utilization is high "
                    f"({cpu}% > 80%)."
                )

            if mem > 80.0:
                rejection_reasons.append(
                    f"Scale-down protection: Memory utilization is high "
                    f"({mem}% > 80%)."
                )

            if traffic > 5000:
                rejection_reasons.append(
                    f"Scale-down protection: Traffic RPM is high "
                    f"({traffic} > 5000)."
                )

            if latency > 300.0:
                rejection_reasons.append(
                    f"Scale-down protection: Latency is high "
                    f"({latency}ms > 300ms)."
                )

        is_approved = len(rejection_reasons) == 0

        return SafetyCheckResult(
            is_approved=is_approved,
            proposal_version=proposal.observation_version,
            evaluated_against_version=observation.state_version,
            rejection_reasons=rejection_reasons,
            applied_rules=applied_rules,
        )