"""Integration tests for FastAPI REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import reset_dependencies
from backend.main import app
from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.execution import ExecutionStatus


@pytest.fixture(autouse=True)
def clean_state():
    """Reset repository state before each API test."""
    reset_dependencies()
    yield
    reset_dependencies()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Health & Root Endpoint Tests
# ---------------------------------------------------------------------------

def test_health_check(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service_count"] == 6
    assert "cart-service" in data["services"]

    api_health_res = client.get("/api/health")
    assert api_health_res.status_code == 200


def test_root_metadata(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Cloud Cost Optimization Agent API"
    assert data["docs_url"] == "/docs"


# ---------------------------------------------------------------------------
# Services Endpoints Tests
# ---------------------------------------------------------------------------

def test_list_services(client: TestClient) -> None:
    res = client.get("/api/services")
    assert res.status_code == 200
    services = res.json()
    assert len(services) == 6
    service_ids = {s["service_id"] for s in services}
    assert "cart-service" in service_ids
    assert "prod-db" in service_ids


def test_get_service_by_id(client: TestClient) -> None:
    res = client.get("/api/services/cart-service")
    assert res.status_code == 200
    data = res.json()
    assert data["service_id"] == "cart-service"
    assert data["current_instances"] == 4
    assert data["cost_per_hour"] == 1.60

    missing_res = client.get("/api/services/unknown-service")
    assert missing_res.status_code == 404


def test_get_service_observation(client: TestClient) -> None:
    res = client.get("/api/services/cart-service/observation")
    assert res.status_code == 200
    obs = res.json()
    assert obs["service_id"] == "cart-service"
    assert obs["cpu_utilization_percent"] == 12.0
    assert obs["memory_utilization_percent"] == 25.0
    assert obs["cost_per_hour"] == 1.60
    assert obs["state_version"] == "v1"

    missing_res = client.get("/api/services/nonexistent/observation")
    assert missing_res.status_code == 404


def test_register_service(client: TestClient) -> None:
    new_service = {
        "service_id": "notification-worker",
        "current_instances": 2,
        "min_instances": 1,
        "max_instances": 5,
        "latency_ms": 15.0,
        "max_latency_ms": 200.0,
        "traffic_rpm": 100,
        "healthy": True,
        "state_version": "v1",
        "cost_per_hour": 0.50,
        "service_type": "batch",
        "is_critical": False,
    }
    res = client.post("/api/services", json=new_service)
    assert res.status_code == 201
    assert res.json()["service_id"] == "notification-worker"

    # Duplicate registration should return 409
    dup_res = client.post("/api/services", json=new_service)
    assert dup_res.status_code == 409


def test_update_metrics(client: TestClient) -> None:
    payload = {
        "latency_ms": 85.0,
        "cpu_utilization_percent": 35.0,
        "bump_version": True,
    }
    res = client.patch("/api/services/cart-service/metrics", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["latency_ms"] == 85.0
    assert data["cpu_utilization_percent"] == 35.0
    assert data["state_version"] == "v2"


def test_update_capacity(client: TestClient) -> None:
    payload = {
        "current_instances": 6,
        "max_instances": 15,
        "bump_version": True,
    }
    res = client.patch("/api/services/cart-service/capacity", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["current_instances"] == 6
    assert data["max_instances"] == 15
    assert data["state_version"] == "v2"


def test_delete_service(client: TestClient) -> None:
    res = client.delete("/api/services/cart-service")
    assert res.status_code == 204

    get_res = client.get("/api/services/cart-service")
    assert get_res.status_code == 404


def test_reset_services(client: TestClient) -> None:
    # Delete cart-service
    client.delete("/api/services/cart-service")
    assert client.get("/api/services/cart-service").status_code == 404

    # Reset
    reset_res = client.post("/api/services/reset")
    assert reset_res.status_code == 200
    assert len(reset_res.json()) == 6
    assert client.get("/api/services/cart-service").status_code == 200


# ---------------------------------------------------------------------------
# Safety Endpoints Tests
# ---------------------------------------------------------------------------

def test_evaluate_safety_approved(client: TestClient) -> None:
    proposal = {
        "action": "scale_down",
        "target_service_id": "cart-service",
        "reason": "Traffic is low.",
        "expected_effect": "Save costs.",
        "observation_version": "v1",
        "confidence": 0.95,
        "target_instances": 2,
    }
    res = client.post("/api/safety/evaluate", json={"proposal": proposal})
    assert res.status_code == 200
    data = res.json()
    assert data["is_approved"] is True
    assert len(data["rejection_reasons"]) == 0


def test_evaluate_safety_rejected_protected_db(client: TestClient) -> None:
    proposal = {
        "action": "stop_idle_service",
        "target_service_id": "prod-db",
        "reason": "Save money.",
        "expected_effect": "Stop db.",
        "observation_version": "v1",
        "confidence": 0.99,
    }
    res = client.post("/api/safety/evaluate", json={"proposal": proposal})
    assert res.status_code == 200
    data = res.json()
    assert data["is_approved"] is False
    assert any("protected" in r.lower() for r in data["rejection_reasons"])


def test_safety_status_and_clear_cooldown(client: TestClient) -> None:
    status_res = client.get("/api/safety/status/prod-db")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["service_id"] == "prod-db"
    assert data["is_protected"] is True
    assert data["is_in_cooldown"] is False

    clear_res = client.post("/api/safety/cooldown/clear?service_id=prod-db")
    assert clear_res.status_code == 200


# ---------------------------------------------------------------------------
# Actions Endpoints Tests
# ---------------------------------------------------------------------------

def test_execute_action_success_and_history(client: TestClient) -> None:
    proposal = {
        "action": "scale_down",
        "target_service_id": "cart-service",
        "reason": "Low traffic.",
        "expected_effect": "Scale down from 4 to 2.",
        "observation_version": "v1",
        "confidence": 0.92,
        "target_instances": 2,
    }
    exec_res = client.post(
        "/api/actions/execute",
        json={"proposal": proposal, "enforce_freshness": True},
    )
    assert exec_res.status_code == 200
    res_data = exec_res.json()
    assert res_data["status"] == "success"
    assert res_data["new_state_version"] == "v2"

    # Verify history
    hist_res = client.get("/api/actions/history")
    assert hist_res.status_code == 200
    history = hist_res.json()
    assert len(history) == 1
    assert history[0]["target_service_id"] == "cart-service"
    assert history[0]["status"] == "success"

    # Delete history
    del_res = client.delete("/api/actions/history")
    assert del_res.status_code == 204
    assert len(client.get("/api/actions/history").json()) == 0


def test_execute_action_stale_rejection(client: TestClient) -> None:
    proposal = {
        "action": "scale_down",
        "target_service_id": "cart-service",
        "reason": "Based on outdated version.",
        "expected_effect": "Scale down.",
        "observation_version": "v0_stale",
        "confidence": 0.92,
    }
    exec_res = client.post(
        "/api/actions/execute",
        json={"proposal": proposal, "enforce_freshness": True},
    )
    assert exec_res.status_code == 200
    res_data = exec_res.json()
    assert res_data["status"] == "failure"


def test_simulate_failure_injection(client: TestClient) -> None:
    # Configure simulated failure
    sim_res = client.post(
        "/api/actions/simulate-failure",
        json={"service_id": "cart-service", "error_code": "capacity_unavailable"},
    )
    assert sim_res.status_code == 200

    proposal = {
        "action": "scale_up",
        "target_service_id": "cart-service",
        "reason": "Traffic spike.",
        "expected_effect": "Scale up.",
        "observation_version": "v1",
        "confidence": 0.95,
        "target_instances": 6,
    }
    exec_res = client.post("/api/actions/execute", json={"proposal": proposal})
    assert exec_res.status_code == 200
    assert exec_res.json()["status"] == "failure"
    assert exec_res.json()["error_code"] == "capacity_unavailable"

    # Clear simulated failure
    clear_sim = client.delete("/api/actions/simulate-failure?service_id=cart-service")
    assert clear_sim.status_code == 200

    # Retry should now succeed
    retry_res = client.post("/api/actions/execute", json={"proposal": proposal})
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "success"
