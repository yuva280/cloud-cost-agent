from datetime import datetime, timezone
from typing import List

from backend.schemas.metrics import ServiceObservation
from backend.schemas.workflow import InvestigationResult


class InvestigationAgent:
    """
    Analyzes cloud service observations and produces an InvestigationResult.
    The agent is deterministic and evaluates predefined rules.
    It does not execute infrastructure actions.
    """

    def __init__(self, staleness_threshold_seconds: int = 300):
        self.staleness_threshold_seconds = staleness_threshold_seconds

    def investigate(self, observation: ServiceObservation) -> InvestigationResult:
        """
        Analyzes the observation and returns an InvestigationResult containing
        identified issues and a summary.
        """
        issues: List[str] = []
        summary_parts: List[str] = []

        # 1. Staleness Check
        now = datetime.now(timezone.utc)
        obs_time = observation.observation_timestamp

        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)

        time_diff = (now - obs_time).total_seconds()
        is_stale = time_diff > self.staleness_threshold_seconds

        if is_stale:
            issues.append("potentially stale observation")
            summary_parts.append(
                f"Observation is stale ({time_diff:.1f}s old)."
            )
            summary_parts.append(
                "This observation should not be used alone for a current "
                "scaling or cost-saving decision."
            )

        # 2. Extract metrics
        cpu = observation.cpu_utilization_percent
        mem = observation.memory_utilization_percent
        traffic = observation.traffic_rpm
        previous_traffic = observation.previous_traffic_rpm
        latency = observation.latency_ms
        cost = observation.cost_per_hour

        # 3. Current resource / latency conditions
        is_high_cpu = cpu > 80.0
        is_high_mem = mem > 80.0
        is_substantial_traffic = traffic > 5000
        is_high_latency = latency > 300.0

        is_low_cpu = cpu <= 20.0
        is_low_mem = mem <= 20.0
        is_low_traffic = traffic <= 1000
        has_meaningful_cost = cost >= 10.0

        # 4. Rising traffic detection
        has_previous_traffic = previous_traffic > 0
        traffic_increased = (
            has_previous_traffic and traffic > previous_traffic
        )

        traffic_increase_ratio = (
            traffic / previous_traffic
            if has_previous_traffic
            else 0.0
        )

        # Treat a >= 50% increase as meaningful rising traffic.
        is_rising_traffic = (
            traffic_increased
            and traffic_increase_ratio >= 1.5
        )

        if is_rising_traffic:
            issues.append("rising traffic")
            summary_parts.append(
                f"Traffic is rising significantly "
                f"({previous_traffic:.0f} RPM to {traffic:.0f} RPM, "
                f"{traffic_increase_ratio:.1f}x increase)."
            )

            if latency >= 250.0:
                issues.append("scale-pressure conditions")
                summary_parts.append(
                    f"Traffic growth is approaching the latency limit "
                    f"({latency:.0f}ms observed versus the service limit). "
                    "Reducing capacity could threaten availability."
                )

        # 5. Existing scale-pressure rules
        if (
            is_high_cpu
            and is_high_mem
            and is_substantial_traffic
            and is_high_latency
            and "scale-pressure conditions" not in issues
        ):
            issues.append("scale-pressure conditions")
            summary_parts.append(
                "Service is experiencing scale pressure "
                "(high CPU, high memory, substantial traffic, and high latency)."
            )
        else:
            if is_high_cpu:
                issues.append("high CPU pressure")
                summary_parts.append("CPU pressure is high.")

            if is_high_mem:
                issues.append("high memory pressure")
                summary_parts.append("Memory pressure is high.")

            if is_substantial_traffic and traffic > 10000:
                issues.append("high traffic")
                summary_parts.append("Traffic is high.")

            if is_high_latency and latency > 500.0:
                issues.append("latency pressure")
                summary_parts.append("Latency is high.")

        # 6. Underutilization
        # Never classify stale or rising-traffic observations as underutilized.
        if not is_stale and not is_rising_traffic:
            is_healthy = observation.healthy is not False
            is_non_critical = observation.is_critical is False

            has_excess_capacity = (
                (observation.current_instances or 0)
                > (observation.min_instances or 0)
            )

            if (
                is_low_cpu
                and is_low_mem
                and is_low_traffic
                and is_healthy
                and is_non_critical
                and has_excess_capacity
            ):
                issues.append("potential underutilization")
                summary_parts.append(
                    "Service is potentially underutilized relative to its capacity."
                )

            elif cost > 100.0 and not (is_substantial_traffic or is_high_cpu):
                issues.append("excessive cost relative to utilization")
                summary_parts.append(
                    "Cost is excessive given the current utilization."
                )

        # 7. Normal operation
        if not issues or (
            len(issues) == 1
            and "potentially stale observation" in issues
        ):
            if not is_stale:
                issues.append("normal operation")
                summary_parts.append(
                    "Service is operating normally."
                )

        summary = " ".join(summary_parts)

        return InvestigationResult(
            observation=observation,
            identified_issues=issues,
            summary=summary
        )