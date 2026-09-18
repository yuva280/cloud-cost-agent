"""Unit tests for ServiceStateManager."""

import concurrent.futures
from datetime import datetime, timezone
import pytest

from backend.schemas.metrics import ServiceObservation
from backend.schemas.service_state import ServiceState
from backend.services.state_manager import (
    InvalidStateUpdateError,
    ServiceAlreadyExistsError,
    ServiceNotFoundError,
    ServiceStateManager,
    StateVersionConflictError,
    generate_next_version,
)


@pytest.fixture
def sample_service() -> ServiceState:
    return ServiceState(
        service_id="cart-service",
        current_instances=3,
        min_instances=1,
        max_instances=10,
        latency_ms=50.0,
        max_latency_ms=200.0,
        traffic_rpm=500,
        healthy=True,
        state_version="v1",
        cpu_utilization_percent=25.0,
        memory_utilization_percent=40.0,
        cost_per_hour=1.20,
        service_type="web",
        is_critical=False,
    )


@pytest.fixture
def state_manager() -> ServiceStateManager:
    manager = ServiceStateManager()
    manager.reset()
    return manager


# ---------------------------------------------------------------------------
# Version Generation Tests
# ---------------------------------------------------------------------------

def test_generate_next_version_sequential() -> None:
    assert generate_next_version("v1") == "v2"
    assert generate_next_version("v9") == "v10"
    assert generate_next_version("v99") == "v100"


def test_generate_next_version_empty_or_non_standard() -> None:
    assert generate_next_version("") == "v1"
    non_standard = generate_next_version("init_hash_123")
    assert non_standard.startswith("v")
    assert len(non_standard) > 1


# ---------------------------------------------------------------------------
# Registration & Catalog Tests
# ---------------------------------------------------------------------------

def test_register_and_get_service(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    registered = state_manager.register_service(sample_service)
    assert registered.service_id == "cart-service"
    assert registered.current_instances == 3

    fetched = state_manager.get_service("cart-service")
    assert fetched is not None
    assert fetched.service_id == "cart-service"
    assert fetched.state_version == "v1"

    # Case-insensitive lookup
    fetched_upper = state_manager.get_service("CART-SERVICE")
    assert fetched_upper is not None
    assert fetched_upper.service_id == "cart-service"


def test_register_duplicate_raises_error(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    with pytest.raises(ServiceAlreadyExistsError):
        state_manager.register_service(sample_service)


def test_register_duplicate_with_overwrite(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    updated_service = sample_service.model_copy(update={"current_instances": 5})
    state_manager.register_service(updated_service, allow_overwrite=True)
    fetched = state_manager.get_service_or_raise("cart-service")
    assert fetched.current_instances == 5


def test_register_invalid_id_raises_error(state_manager: ServiceStateManager) -> None:
    with pytest.raises(InvalidStateUpdateError):
        state_manager.register_service(
            ServiceState(
                service_id="   ",
                current_instances=1,
                latency_ms=10.0,
                state_version="v1",
            )
        )


def test_get_non_existent_service(state_manager: ServiceStateManager) -> None:
    assert state_manager.get_service("missing-service") is None
    with pytest.raises(ServiceNotFoundError):
        state_manager.get_service_or_raise("missing-service")


def test_service_exists(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    assert not state_manager.service_exists("cart-service")
    state_manager.register_service(sample_service)
    assert state_manager.service_exists("cart-service")
    assert state_manager.service_exists("CART-SERVICE")
    assert not state_manager.service_exists("")


def test_list_services_immutability(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    services = state_manager.list_services()
    assert len(services) == 1
    # Mutating returned object must not mutate internal state
    services[0].current_instances = 99
    fetched = state_manager.get_service_or_raise("cart-service")
    assert fetched.current_instances == 3


def test_delete_service(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    assert state_manager.delete_service("cart-service") is True
    assert state_manager.delete_service("cart-service") is False
    assert state_manager.get_service("cart-service") is None


# ---------------------------------------------------------------------------
# Observation Snapshot Tests
# ---------------------------------------------------------------------------

def test_create_observation(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    obs = state_manager.create_observation("cart-service")

    assert isinstance(obs, ServiceObservation)
    assert obs.service_id == "cart-service"
    assert obs.cpu_utilization_percent == 25.0
    assert obs.memory_utilization_percent == 40.0
    assert obs.traffic_rpm == 500
    assert obs.latency_ms == 50.0
    assert obs.cost_per_hour == 1.20
    assert obs.state_version == "v1"
    assert obs.observation_timestamp.tzinfo == timezone.utc


def test_create_observation_missing_service_raises(state_manager: ServiceStateManager) -> None:
    with pytest.raises(ServiceNotFoundError):
        state_manager.create_observation("missing-service")


# ---------------------------------------------------------------------------
# Metrics Update Tests
# ---------------------------------------------------------------------------

def test_update_metrics_success_and_version_bump(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    updated = state_manager.update_metrics(
        service_id="cart-service",
        cpu_utilization_percent=55.0,
        memory_utilization_percent=60.0,
        traffic_rpm=800,
        latency_ms=75.0,
        cost_per_hour=1.50,
        healthy=False,
        bump_version=True,
    )

    assert updated.cpu_utilization_percent == 55.0
    assert updated.memory_utilization_percent == 60.0
    assert updated.traffic_rpm == 800
    assert updated.latency_ms == 75.0
    assert updated.cost_per_hour == 1.50
    assert updated.healthy is False
    assert updated.state_version == "v2"


def test_update_metrics_without_version_bump(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    updated = state_manager.update_metrics(
        service_id="cart-service",
        latency_ms=62.0,
        bump_version=False,
    )
    assert updated.latency_ms == 62.0
    assert updated.state_version == "v1"


def test_update_metrics_bounds_validation(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_metrics("cart-service", cpu_utilization_percent=105.0)

    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_metrics("cart-service", memory_utilization_percent=-5.0)

    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_metrics("cart-service", traffic_rpm=-1)

    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_metrics("cart-service", latency_ms=-10.0)

    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_metrics("cart-service", cost_per_hour=-0.5)


# ---------------------------------------------------------------------------
# Capacity Update Tests
# ---------------------------------------------------------------------------

def test_update_capacity_success(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)
    updated = state_manager.update_capacity(
        service_id="cart-service",
        current_instances=5,
        min_instances=2,
        max_instances=12,
        bump_version=True,
    )

    assert updated.current_instances == 5
    assert updated.min_instances == 2
    assert updated.max_instances == 12
    assert updated.state_version == "v2"


def test_update_capacity_relational_violation(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    # min_instances cannot exceed max_instances
    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_capacity("cart-service", min_instances=15, max_instances=10)

    # current_instances cannot be negative
    with pytest.raises(InvalidStateUpdateError):
        state_manager.update_capacity("cart-service", current_instances=-1)


# ---------------------------------------------------------------------------
# Optimistic Concurrency Control Tests
# ---------------------------------------------------------------------------

def test_optimistic_update_state_success(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    modified = sample_service.model_copy(
        update={"current_instances": 4, "state_version": "v2"}
    )
    res = state_manager.update_state("cart-service", modified, expected_version="v1")
    assert res.current_instances == 4
    assert res.state_version == "v2"


def test_optimistic_update_state_conflict_raises(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    modified = sample_service.model_copy(
        update={"current_instances": 4, "state_version": "v2"}
    )
    with pytest.raises(StateVersionConflictError):
        state_manager.update_state("cart-service", modified, expected_version="v0_stale")


# ---------------------------------------------------------------------------
# Seed Catalog Tests
# ---------------------------------------------------------------------------

def test_seed_default_services(state_manager: ServiceStateManager) -> None:
    seeded = state_manager.seed_default_services()
    assert len(seeded) == 6

    ids = set(state_manager.list_service_ids())
    expected_ids = {
        "cart-service",
        "auth-service",
        "payment-gateway",
        "analytics-worker",
        "prod-db",
        "spiky-api",
    }
    assert ids == expected_ids

    # Verify attributes of critical and specialized services
    prod_db = state_manager.get_service_or_raise("prod-db")
    assert prod_db.is_critical is True
    assert prod_db.service_type == "database"

    worker = state_manager.get_service_or_raise("analytics-worker")
    assert worker.service_type == "batch"
    assert worker.traffic_rpm == 0

    spiky = state_manager.get_service_or_raise("spiky-api")
    assert spiky.latency_ms == 195.0
    assert spiky.max_latency_ms == 200.0


# ---------------------------------------------------------------------------
# Thread Safety Test
# ---------------------------------------------------------------------------

def test_concurrent_metric_updates(state_manager: ServiceStateManager, sample_service: ServiceState) -> None:
    state_manager.register_service(sample_service)

    def worker_update(idx: int) -> None:
        state_manager.update_metrics(
            "cart-service",
            cpu_utilization_percent=float(idx % 100),
            bump_version=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_update, i) for i in range(50)]
        concurrent.futures.wait(futures)

    final_state = state_manager.get_service_or_raise("cart-service")
    assert final_state is not None
    # 50 sequential version bumps from v1 -> v51
    assert final_state.state_version == "v51"
