from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone

from backend.services.state_manager import ServiceStateManager
from backend.schemas.service_state import ServiceState
from backend.schemas.metrics import ServiceObservation

class EnvironmentParser:
    """
    Parses an uploaded JSON environment dictionary and temporarily seeds the StateManager,
    extracts explicit ServiceObservations (if any), and identifies simulated execution failures.
    """
    def __init__(self, state_manager: ServiceStateManager):
        self.state_manager = state_manager

    def parse(self, env_data: Dict[str, Any]) -> Tuple[Optional[ServiceObservation], Optional[Dict[str, Any]]]:
        """
        Parses the JSON data.
        Returns:
            - explicit_observation: A ServiceObservation explicitly defined in the JSON (e.g., with a stale timestamp).
            - simulated_failure: A dictionary representing an execution failure (action, target, error_code).
        """
        explicit_observation = None
        simulated_failure = None

        # Parse services
        services = []
        if "services" in env_data:
            if isinstance(env_data["services"], list):
                services = env_data["services"]
            elif isinstance(env_data["services"], dict):
                # If they passed {"services": {"orders-api": {...}}}
                for k, v in env_data["services"].items():
                    v["service_id"] = k
                    services.append(v)
        else:
            # Maybe the top-level keys are services if they don't match known keywords
            for k, v in env_data.items():
                if isinstance(v, dict) and k not in ["observation", "execution_result", "action result"]:
                    # Likely a service
                    v["service_id"] = k
                    services.append(v)

        for s_data in services:
            self._parse_and_register_service(s_data)

        # Parse explicit observation overrides
        if "observation" in env_data and isinstance(env_data["observation"], dict):
            obs_data = env_data["observation"]
            explicit_observation = self._parse_observation(obs_data)
        else:
            # Check if any service data contains an inline 'timestamp' or
            # 'latest_timestamp' field. If so, create an explicit observation
            # with the observation timestamp so the staleness check in
            # InvestigationAgent works correctly.
            for s_data in services:
                ts_val = s_data.get("timestamp") or s_data.get("observation_timestamp")
                latest_ts = s_data.get("latest_timestamp")
                if ts_val or latest_ts:
                    sid = s_data.get("service_id") or s_data.get("service") or s_data.get("name")
                    if sid:
                        obs_data = {**s_data, "service_id": sid}
                        # Use the primary observation timestamp if available
                        if ts_val:
                            obs_data["timestamp"] = ts_val
                        elif latest_ts:
                            # Only latest_timestamp exists; the absence of a primary
                            # timestamp means the observation has no recorded time,
                            # so mark it with epoch to force staleness detection.
                            obs_data["timestamp"] = "2000-01-01T00:00:00Z"
                        explicit_observation = self._parse_observation(obs_data)
                        break  # Use the first service with timestamp info

        # Parse execution failures
        fail_data = env_data.get("execution_result") or env_data.get("action result")
        if fail_data and isinstance(fail_data, dict):
            if fail_data.get("status", "").lower() == "failure" or fail_data.get("error_code"):
                simulated_failure = {
                    "action": fail_data.get("action", "").upper(),
                    "target": fail_data.get("target") or fail_data.get("target_service_id") or fail_data.get("service_id", ""),
                    "error_code": fail_data.get("error_code", "unknown_error"),
                    "error_message": fail_data.get("error_message", f"Simulated failure from JSON: {fail_data.get('error_code')}")
                }

        return explicit_observation, simulated_failure

    def _parse_and_register_service(self, data: Dict[str, Any]):
        sid = data.get("service_id") or data.get("service") or data.get("name")
        if not sid:
            return

        # Attempt to fetch existing to merge, or create new defaults
        existing = self.state_manager.get_service(sid)
        if existing:
            state_dict = existing.model_dump()
        else:
            state_dict = {
                "service_id": sid,
                "current_instances": 1,
                "min_instances": 1,
                "max_instances": 5,
                "latency_ms": 10.0,
                "max_latency_ms": 500.0,
                "traffic_rpm": 0,
                "healthy": True,
                "state_version": "v1",
                "cpu_utilization_percent": 0.0,
                "memory_utilization_percent": 0.0,
                "cost_per_hour": 1.0,
                "service_type": "unknown",
                "is_critical": False
            }

        # Flexible mapping for user pseudo-JSON formats
        mapping = {
            "CPU": "cpu_utilization_percent",
            "cpu": "cpu_utilization_percent",
            "memory": "memory_utilization_percent",
            "requests per minute": "traffic_rpm",
            "RPM": "traffic_rpm",
            "latency": "latency_ms",
            "instances": "current_instances",
            "cost per hour": "cost_per_hour",
            "min instances": "min_instances",
            "max instances": "max_instances",
            "max latency": "max_latency_ms"
        }

        for k, v in data.items():
            if k in state_dict:
                state_dict[k] = self._clean_numeric(v) if isinstance(v, (int, float, str)) and k != "service_id" else v
            elif k in mapping:
                mapped_k = mapping[k]
                state_dict[mapped_k] = self._clean_numeric(v)

        self.state_manager.register_service(ServiceState(**state_dict), allow_overwrite=True)

    def _parse_observation(self, data: Dict[str, Any]) -> ServiceObservation:
        sid = data.get("service_id") or data.get("service")
        
        # Merge against the state manager for missing fields
        existing = self.state_manager.get_service(sid) if sid else None
        
        ts = datetime.now(timezone.utc)
        if "timestamp" in data:
            try:
                ts_str = data["timestamp"].replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                pass
                
        mapping = {
            "CPU": "cpu_utilization_percent",
            "cpu": "cpu_utilization_percent",
            "memory": "memory_utilization_percent",
            "requests per minute": "traffic_rpm",
            "RPM": "traffic_rpm",
            "latency": "latency_ms",
            "cost per hour": "cost_per_hour",
        }
        
        parsed = {}
        for k, v in data.items():
            if k in mapping:
                parsed[mapping[k]] = self._clean_numeric(v)
            else:
                parsed[k] = v

        return ServiceObservation(
            service_id=sid or "unknown",
            cpu_utilization_percent=parsed.get("cpu_utilization_percent", existing.cpu_utilization_percent if existing else 0.0),
            memory_utilization_percent=parsed.get("memory_utilization_percent", existing.memory_utilization_percent if existing else 0.0),
            traffic_rpm=parsed.get("traffic_rpm", existing.traffic_rpm if existing else 0),
            latency_ms=parsed.get("latency_ms", existing.latency_ms if existing else 0.0),
            cost_per_hour=parsed.get("cost_per_hour", existing.cost_per_hour if existing else 0.0),
            observation_timestamp=ts,
            state_version=parsed.get("state_version", existing.state_version if existing else "v1"),
            current_instances=existing.current_instances if existing else 1,
            min_instances=existing.min_instances if existing else 1,
            healthy=existing.healthy if existing else True,
            is_critical=existing.is_critical if existing else False
        )

    def _clean_numeric(self, val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            # Extract digits/floats (e.g. "$18.50" -> 18.50, "22%" -> 22.0, "180ms" -> 180.0)
            import re
            match = re.search(r"[-+]?\d*\.\d+|\d+", val)
            if match:
                return float(match.group())
        return 0.0
