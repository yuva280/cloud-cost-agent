"""Integration tests for the Workflow Service and FastAPI workflow endpoints."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from backend.api.deps import reset_dependencies
from backend.main import app
from backend.schemas.metrics import ServiceObservation


@pytest.fixture(autouse=True)
def clean_state():
    """Reset repository and workflow state before and after each test."""
    reset_dependencies()
    yield
    reset_dependencies()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# POST /api/workflow/run Tests
# ---------------------------------------------------------------------------


def test_run_workflow_underutilized_cart_service(client: TestClient) -> None:
    """Underutilized service workflow triggers scale_down, succeeds, and verifies new version."""
    # Configure cart-service with high cost and low utilization to trigger scale_down
    update_res = client.patch(
        "/api/services/cart-service/metrics",
        json={
            "cpu_utilization_percent": 10.0,
            "memory_utilization_percent": 15.0,
            "traffic_rpm": 100,
            "latency_ms": 35.0,
            "cost_per_hour": 24.0,
        },
    )
    assert update_res.status_code == 200

    # Run workflow on cart-service
    res = client.post("/api/workflow/run", json={"service_id": "cart-service"})
    assert res.status_code == 200
    report = res.json()

    assert "workflow_id" in report
    assert report["initial_observation"]["service_id"] == "cart-service"

    verification = report["final_verification"]
    assert verification["is_successful"] is True

    # Decision proposed scale_down
    decision = verification["decision"]
    assert decision["proposal"]["action"] == "scale_down"
    assert decision["proposal"]["target_service_id"] == "cart-service"

    # Safety check approved
    assert verification["safety_check"]["is_approved"] is True

    # Execution succeeded and advanced state version
    execution = verification["execution"]
    assert execution is not None
    assert execution["status"] == "success"
    assert execution["action"] == "scale_down"
    assert execution["new_state_version"] is not None

    # Post-execution verification notes confirmed version advancement
    assert "Post-execution observation confirms the state version changed" in verification["verification_notes"]

    # Verify live service state in state manager was mutated
    svc_res = client.get("/api/services/cart-service")
    assert svc_res.status_code == 200
    svc_data = svc_res.json()
    assert svc_data["current_instances"] == 3  # Scaled down from 4 to 3
    assert svc_data["state_version"] == execution["new_state_version"]


def test_run_workflow_stale_observation_proposes_no_action(client: TestClient) -> None:
    """Stale observations must not trigger disruptive actions and safely resolve to NO_ACTION."""
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    stale_obs = ServiceObservation(
        service_id="cart-service",
        cpu_utilization_percent=5.0,
        memory_utilization_percent=5.0,
        traffic_rpm=10,
        latency_ms=20.0,
        cost_per_hour=50.0,
        state_version="v1",
        observation_timestamp=stale_time,
    )

    res = client.post("/api/workflow/run", json={"observation": stale_obs.model_dump(mode="json")})
    assert res.status_code == 200
    report = res.json()

    verification = report["final_verification"]
    assert verification["is_successful"] is True
    assert verification["decision"]["proposal"]["action"] == "no_action"
    assert verification["execution"] is None
    assert "No infrastructure change was required" in verification["verification_notes"]


def test_run_workflow_simulated_failure_payment_api(client: TestClient) -> None:
    """Simulated provider capacity failure on scale_up must report failure and escalation notes."""
    # Create an observation for payment-api experiencing scale pressure
    obs = ServiceObservation(
        service_id="payment-api",
        cpu_utilization_percent=92.0,
        memory_utilization_percent=88.0,
        traffic_rpm=7500,
        latency_ms=380.0,
        cost_per_hour=30.0,
        state_version="v1",
        observation_timestamp=datetime.now(timezone.utc),
    )

    res = client.post("/api/workflow/run", json={"observation": obs.model_dump(mode="json")})
    assert res.status_code == 200
    report = res.json()

    verification = report["final_verification"]
    assert verification["is_successful"] is False
    assert verification["decision"]["proposal"]["action"] == "scale_up"
    assert verification["safety_check"]["is_approved"] is True

    execution = verification["execution"]
    assert execution is not None
    assert execution["status"] == "failure"
    assert execution["error_code"] == "capacity_unavailable"
    assert execution["new_state_version"] is None

    # Verification notes report provider failure
    assert "Execution failed (Code: capacity_unavailable)" in verification["verification_notes"]


def test_run_workflow_validation_errors(client: TestClient) -> None:
    """Validation checks for missing payload parameters and non-existent services."""
    # Neither service_id nor observation provided
    res_empty = client.post("/api/workflow/run", json={})
    assert res_empty.status_code == 400
    assert "Either 'service_id' or 'observation' must be provided" in res_empty.json()["detail"]

    # Target service does not exist
    res_404 = client.post("/api/workflow/run", json={"service_id": "non-existent-service-1234"})
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"]


# ---------------------------------------------------------------------------
# Reports Endpoints: GET /api/workflow/reports & GET /api/workflow/reports/{id}
# ---------------------------------------------------------------------------


def test_get_workflow_reports_and_filtering(client: TestClient) -> None:
    """List workflow execution reports with optional service ID filtering and limit."""
    # Run two workflows
    client.post("/api/workflow/run", json={"service_id": "cart-service"})
    client.post("/api/workflow/run", json={"service_id": "auth-service"})

    # List all reports
    res_all = client.get("/api/workflow/reports")
    assert res_all.status_code == 200
    reports = res_all.json()
    assert len(reports) == 2

    # Filter by service_id
    res_filtered = client.get("/api/workflow/reports?service_id=cart-service")
    assert res_filtered.status_code == 200
    filtered_reports = res_filtered.json()
    assert len(filtered_reports) == 1
    assert filtered_reports[0]["initial_observation"]["service_id"] == "cart-service"

    # Test limit parameter
    res_limit = client.get("/api/workflow/reports?limit=1")
    assert res_limit.status_code == 200
    assert len(res_limit.json()) == 1


def test_get_workflow_report_by_id(client: TestClient) -> None:
    """Retrieve an individual report by workflow_id, or return 404 if not found."""
    run_res = client.post("/api/workflow/run", json={"service_id": "cart-service"})
    assert run_res.status_code == 200
    wf_id = run_res.json()["workflow_id"]

    # Existing report
    res = client.get(f"/api/workflow/reports/{wf_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["workflow_id"] == wf_id
    assert data["initial_observation"]["service_id"] == "cart-service"

    # Non-existent report
    res_404 = client.get("/api/workflow/reports/non-existent-workflow-id")
    assert res_404.status_code == 404


# ---------------------------------------------------------------------------
# Savings & History Endpoints: GET /api/workflow/savings & DELETE /api/workflow/reports
# ---------------------------------------------------------------------------


def test_get_savings_summary(client: TestClient) -> None:
    """Verify cost savings summary aggregation and financial projections."""
    # Before running workflows
    res_empty = client.get("/api/workflow/savings")
    assert res_empty.status_code == 200
    summary = res_empty.json()
    assert summary["total_workflows_run"] == 0
    assert summary["total_hourly_savings"] == 0.0

    # Configure and run underutilized cart-service workflow to generate savings
    client.patch(
        "/api/services/cart-service/metrics",
        json={
            "cpu_utilization_percent": 8.0,
            "memory_utilization_percent": 12.0,
            "traffic_rpm": 80,
            "cost_per_hour": 20.0,
        },
    )
    run_res = client.post("/api/workflow/run", json={"service_id": "cart-service"})
    assert run_res.status_code == 200

    # Check updated savings
    res_savings = client.get("/api/workflow/savings")
    assert res_savings.status_code == 200
    data = res_savings.json()

    assert data["total_workflows_run"] == 1
    assert data["successful_optimizations"] == 1
    assert data["total_hourly_savings"] > 0.0
    assert data["projected_monthly_savings"] == round(data["total_hourly_savings"] * 730.0, 2)
    assert data["projected_annual_savings"] == round(data["total_hourly_savings"] * 8760.0, 2)
    assert "cart-service" in data["optimized_services"]
    assert "cart-service" in data["per_service_savings"]


def test_clear_workflow_reports(client: TestClient) -> None:
    """DELETE /api/workflow/reports clears audit history and resets savings metrics."""
    client.post("/api/workflow/run", json={"service_id": "cart-service"})
    assert len(client.get("/api/workflow/reports").json()) == 1

    del_res = client.delete("/api/workflow/reports")
    assert del_res.status_code == 204

    # Reports should be empty
    assert len(client.get("/api/workflow/reports").json()) == 0

    # Savings should be reset
    savings = client.get("/api/workflow/savings").json()
    assert savings["total_workflows_run"] == 0
    assert savings["total_hourly_savings"] == 0.0
