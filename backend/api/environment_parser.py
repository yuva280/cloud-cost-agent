from typing import Dict, Any, Tuple, Optional, Union
from datetime import datetime, timezone

from backend.services.state_manager import ServiceStateManager
from backend.schemas.service_state import ServiceState
from backend.schemas.metrics import ServiceObservation


class EnvironmentParser:
    """
    Parses an uploaded JSON environment, seeds the StateManager,
    extracts explicit ServiceObservations, and identifies simulated
    execution failures.
    """

    def __init__(self, state_manager: ServiceStateManager):
        self.state_manager = state_manager

    def parse(
        self,
        env_data: Union[Dict[str, Any], list]
    ) -> Tuple[Optional[ServiceObservation], Optional[Dict[str, Any]]]:

        explicit_observation = None
        simulated_failure = None

        # ---------------------------------------------------------
        # 1. Normalize top-level JSON format
        # ---------------------------------------------------------
        # Supports:
        #
        # [
        #   {"service_id": "payment-api", ...}
        # ]
        #
        # and:
        #
        # {
        #   "services": [...]
        # }
        #
        # and:
        #
        # {
        #   "services": {
        #       "payment-api": {...}
        #   }
        # }
        # ---------------------------------------------------------

        if isinstance(env_data, list):
            env_data = {"services": env_data}

        if not isinstance(env_data, dict):
            raise ValueError("Environment JSON must be an object or a list.")

        # ---------------------------------------------------------
        # 2. Parse services
        # ---------------------------------------------------------

        services = []

        if "services" in env_data:

            if isinstance(env_data["services"], list):
                services = env_data["services"]

            elif isinstance(env_data["services"], dict):
                for service_id, service_data in env_data["services"].items():

                    if not isinstance(service_data, dict):
                        continue

                    service_data = dict(service_data)
                    service_data["service_id"] = service_id

                    services.append(service_data)

        else:
            # Support top-level service objects
            for key, value in env_data.items():

                if (
                    isinstance(value, dict)
                    and key not in [
                        "observation",
                        "execution_result",
                        "action result"
                    ]
                ):
                    service_data = dict(value)
                    service_data["service_id"] = key
                    services.append(service_data)

        # Register all services
        for service_data in services:

            if isinstance(service_data, dict):
                self._parse_and_register_service(service_data)

        # ---------------------------------------------------------
        # 3. Parse explicit observation
        # ---------------------------------------------------------

        if (
            "observation" in env_data
            and isinstance(env_data["observation"], dict)
        ):
            explicit_observation = self._parse_observation(
                env_data["observation"]
            )

        else:

            # Look for timestamp information inside service data.
            #
            # This is important for Challenge 1 / stale observation.
            for service_data in services:

                if not isinstance(service_data, dict):
                    continue

                timestamp_value = (
                    service_data.get("timestamp")
                    or service_data.get("observation_timestamp")
                )

                latest_timestamp = service_data.get(
                    "latest_timestamp"
                )

                if timestamp_value or latest_timestamp:

                    service_id = (
                        service_data.get("service_id")
                        or service_data.get("service")
                        or service_data.get("name")
                    )

                    if service_id:

                        observation_data = {
                            **service_data,
                            "service_id": service_id
                        }

                        if timestamp_value:

                            observation_data["timestamp"] = (
                                timestamp_value
                            )

                        elif latest_timestamp:

                            # If only latest_timestamp is available,
                            # there is no reliable observation timestamp.
                            # Force stale detection.
                            observation_data["timestamp"] = (
                                "2000-01-01T00:00:00Z"
                            )

                        explicit_observation = (
                            self._parse_observation(
                                observation_data
                            )
                        )

                        break

        # ---------------------------------------------------------
        # 4. Parse simulated execution failures
        # ---------------------------------------------------------

        failure_data = (
            env_data.get("execution_result")
            or env_data.get("action result")
        )

        if (
            failure_data
            and isinstance(failure_data, dict)
        ):

            if (
                failure_data.get("status", "").lower() == "failure"
                or failure_data.get("error_code")
            ):

                simulated_failure = {
                    "action": failure_data.get(
                        "action",
                        ""
                    ).upper(),

                    "target": (
                        failure_data.get("target")
                        or failure_data.get(
                            "target_service_id"
                        )
                        or failure_data.get(
                            "service_id",
                            ""
                        )
                    ),

                    "error_code": failure_data.get(
                        "error_code",
                        "unknown_error"
                    ),

                    "error_message": failure_data.get(
                        "error_message",
                        (
                            "Simulated failure from JSON: "
                            f"{failure_data.get('error_code')}"
                        )
                    )
                }

        return explicit_observation, simulated_failure

    # =============================================================
    # SERVICE REGISTRATION
    # =============================================================

    def _parse_and_register_service(
        self,
        data: Dict[str, Any]
    ):

        service_id = (
            data.get("service_id")
            or data.get("service")
            or data.get("name")
        )

        if not service_id:
            return

        # ---------------------------------------------------------
        # Existing service
        # ---------------------------------------------------------

        existing = self.state_manager.get_service(service_id)

        if existing:

            state_dict = existing.model_dump()

        else:

            state_dict = {
                "service_id": service_id,
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

        # ---------------------------------------------------------
        # Flexible JSON field mapping
        # ---------------------------------------------------------

        mapping = {

            # CPU
            "CPU": "cpu_utilization_percent",
            "cpu": "cpu_utilization_percent",
            "cpu_percent": "cpu_utilization_percent",

            # Memory
            "memory": "memory_utilization_percent",
            "memory_percent": "memory_utilization_percent",

            # Traffic
            "requests per minute": "traffic_rpm",
            "RPM": "traffic_rpm",
            "traffic_rpm": "traffic_rpm",

            # Previous traffic
            "previous RPM": "previous_traffic_rpm",
            "previous_traffic_rpm": "previous_traffic_rpm",

            # Latency
            "latency": "latency_ms",
            "latency_ms": "latency_ms",

            # Instances
            "instances": "current_instances",
            "current_instances": "current_instances",

            "min instances": "min_instances",
            "min_instances": "min_instances",

            "max instances": "max_instances",
            "max_instances": "max_instances",

            # Cost
            "cost per hour": "cost_per_hour",
            "cost_per_hour": "cost_per_hour",

            # Latency limit
            "max latency": "max_latency_ms",
            "max_latency_ms": "max_latency_ms",
        }

        # ---------------------------------------------------------
        # Apply mappings
        # ---------------------------------------------------------

        for key, value in data.items():

            # Direct schema field
            if key in state_dict:

                if (
                    isinstance(value, (int, float, str))
                    and key != "service_id"
                ):
                    state_dict[key] = self._clean_numeric(value)

                else:
                    state_dict[key] = value

            # Flexible field name
            elif key in mapping:

                mapped_key = mapping[key]

                # Boolean fields should remain boolean
                if mapped_key in ["healthy", "is_critical"]:

                    state_dict[mapped_key] = bool(value)

                elif mapped_key == "state_version":

                    state_dict[mapped_key] = str(value)

                else:

                    state_dict[mapped_key] = (
                        self._clean_numeric(value)
                    )

        # ---------------------------------------------------------
        # Ensure service_id is correct
        # ---------------------------------------------------------

        state_dict["service_id"] = service_id

        # ---------------------------------------------------------
        # Register service
        # ---------------------------------------------------------

        self.state_manager.register_service(
            ServiceState(**state_dict),
            allow_overwrite=True
        )

    # =============================================================
    # OBSERVATION PARSER
    # =============================================================

    def _parse_observation(
        self,
        data: Dict[str, Any]
    ) -> ServiceObservation:

        service_id = (
            data.get("service_id")
            or data.get("service")
            or data.get("name")
        )

        # ---------------------------------------------------------
        # Merge missing values with registered service state
        # ---------------------------------------------------------

        existing = (
            self.state_manager.get_service(service_id)
            if service_id
            else None
        )

        # ---------------------------------------------------------
        # Observation timestamp
        # ---------------------------------------------------------

        timestamp = datetime.now(timezone.utc)

        if "timestamp" in data:

            try:

                timestamp_value = str(
                    data["timestamp"]
                ).replace("Z", "+00:00")

                timestamp = datetime.fromisoformat(
                    timestamp_value
                )

                # Ensure timezone awareness
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(
                        tzinfo=timezone.utc
                    )

            except Exception:

                # Keep current time if timestamp cannot be parsed
                pass

        elif "observation_timestamp" in data:

            try:

                timestamp_value = str(
                    data["observation_timestamp"]
                ).replace("Z", "+00:00")

                timestamp = datetime.fromisoformat(
                    timestamp_value
                )

                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(
                        tzinfo=timezone.utc
                    )

            except Exception:

                pass

        # ---------------------------------------------------------
        # Observation field mappings
        # ---------------------------------------------------------

        mapping = {

            # CPU
            "CPU": "cpu_utilization_percent",
            "cpu": "cpu_utilization_percent",
            "cpu_percent": "cpu_utilization_percent",

            # Memory
            "memory": "memory_utilization_percent",
            "memory_percent": "memory_utilization_percent",

            # Traffic
            "requests per minute": "traffic_rpm",
            "RPM": "traffic_rpm",
            "traffic_rpm": "traffic_rpm",

            # Previous traffic
            "previous RPM": "previous_traffic_rpm",
            "previous_traffic_rpm": "previous_traffic_rpm",

            # Latency
            "latency": "latency_ms",
            "latency_ms": "latency_ms",

            # Cost
            "cost per hour": "cost_per_hour",
            "cost_per_hour": "cost_per_hour",
        }

        parsed = {}

        # ---------------------------------------------------------
        # Parse all fields
        # ---------------------------------------------------------

        for key, value in data.items():

            if key in mapping:

                parsed[mapping[key]] = (
                    self._clean_numeric(value)
                )

            else:

                parsed[key] = value

        # ---------------------------------------------------------
        # Build ServiceObservation
        # ---------------------------------------------------------

        return ServiceObservation(

            service_id=(
                service_id
                or "unknown"
            ),

            cpu_utilization_percent=parsed.get(
                "cpu_utilization_percent",
                (
                    existing.cpu_utilization_percent
                    if existing
                    else 0.0
                )
            ),

            memory_utilization_percent=parsed.get(
                "memory_utilization_percent",
                (
                    existing.memory_utilization_percent
                    if existing
                    else 0.0
                )
            ),

            traffic_rpm=parsed.get(
                "traffic_rpm",
                (
                    existing.traffic_rpm
                    if existing
                    else 0
                )
            ),

            previous_traffic_rpm=parsed.get(
                "previous_traffic_rpm",
                0
            ),

            latency_ms=parsed.get(
                "latency_ms",
                (
                    existing.latency_ms
                    if existing
                    else 0.0
                )
            ),

            cost_per_hour=parsed.get(
                "cost_per_hour",
                (
                    existing.cost_per_hour
                    if existing
                    else 0.0
                )
            ),

            observation_timestamp=timestamp,

            state_version=parsed.get(
                "state_version",
                (
                    existing.state_version
                    if existing
                    else "v1"
                )
            ),

            current_instances=parsed.get(
                "current_instances",
                (
                    existing.current_instances
                    if existing
                    else 1
                )
            ),

            min_instances=parsed.get(
                "min_instances",
                (
                    existing.min_instances
                    if existing
                    else 1
                )
            ),

            healthy=parsed.get(
                "healthy",
                (
                    existing.healthy
                    if existing
                    else True
                )
            ),

            is_critical=parsed.get(
                "is_critical",
                (
                    existing.is_critical
                    if existing
                    else False
                )
            )
        )

    # =============================================================
    # NUMERIC CLEANING
    # =============================================================

    def _clean_numeric(
        self,
        value: Any
    ) -> float:

        if isinstance(value, (int, float)):

            return float(value)

        if isinstance(value, str):

            import re

            match = re.search(
                r"[-+]?\d*\.\d+|\d+",
                value
            )

            if match:

                return float(
                    match.group()
                )

        return 0.0