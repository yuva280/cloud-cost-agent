from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_json_environment_upload_safe_optimization():
    # Test A: Safe Cost Reduction
    env = {
        "services": [
            {
                "service_id": "orders-api",
                "cpu": 22,
                "memory": 41,
                "RPM": 1200,
                "latency": 180,
                "instances": 6,
                "cost per hour": 18.50,
                "min instances": 2,
                "max instances": 8,
                "max latency": 300,
                "healthy": True
            },
            {
                "service_id": "reports-worker",
                "cpu": 9,
                "memory": 15,
                "RPM": 0,
                "instances": 4,
                "cost per hour": 11,
                "min instances": 1,
                "max instances": 6,
                "max latency": 900,
                "healthy": True
            }
        ]
    }
    
    payload = {
        "request": "Review the current services and reduce unnecessary cloud cost without breaking latency or availability requirements.",
        "environment": env
    }
    
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    # NLP should identify reports-worker as the most underutilized service
    assert data["identified_service"] == "reports-worker"
    
    report = data["report"]
    # Check that it proposed scale_down
    assert report["final_verification"]["decision"]["proposal"]["action"] == "scale_down"
    # Check that safety approved it
    assert report["final_verification"]["safety_check"]["is_approved"] is True
    # Check that it executed successfully
    assert report["final_verification"]["execution"]["status"] == "SUCCESS"


def test_json_environment_upload_stale_observation():
    # Test C: Stale Observation
    env = {
        "services": [
            {
                "service_id": "checkout-api",
                "cpu": 24,
                "memory": 39,
                "RPM": 5200,
                "latency": 170,
                "instances": 5
            }
        ],
        "observation": {
            "service_id": "checkout-api",
            "timestamp": "2026-09-17T08:00:00Z",
            "cpu": 24,
            "memory": 39,
            "RPM": 900,
            "latency": 170
        }
    }
    
    payload = {
        "request": "Do not make a scaling or cost-saving decision using stale observations. Investigate the latest traffic before acting.",
        "environment": env
    }
    
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert data["identified_service"] == "checkout-api"
    report = data["report"]
    
    # Safety engine should approve the NO_ACTION proposal which was triggered by staleness
    assert report["final_verification"]["decision"]["proposal"]["action"] == "no_action"
    assert report["final_verification"]["safety_check"]["is_approved"] is True
    # The investigation summary should mention it's stale
    assert "stale" in report["final_verification"]["decision"]["investigation"]["summary"].lower()


def test_json_environment_upload_action_failure():
    # Test D: Action Failure
    env = {
        "payment-api": {
            "CPU": 91,
            "memory": 82,
            "RPM": 6400,
            "latency": 410,
            "instances": 3,
            "max latency": 300
        },
        "action result": {
            "action": "scale_up",
            "target": "payment-api",
            "status": "failure",
            "error_code": "capacity_unavailable"
        }
    }
    
    payload = {
        "request": "Scale the service to maintain availability, but report failure accurately if the infrastructure cannot provide capacity.",
        "environment": env
    }
    
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert data["identified_service"] == "payment-api"
    report = data["report"]
    
    # Should be approved by safety
    assert report["final_verification"]["safety_check"]["is_approved"] is True
    # But execution should fail
    assert report["final_verification"]["execution"]["error_code"] == "capacity_unavailable"
    assert report["final_verification"]["is_successful"] is False

def test_json_environment_upload_single_service():
    # Proves that an environment containing ONLY reports-worker targets reports-worker
    env = {
        "services": [
            {
                "service_id": "reports-worker",
                "cpu": 9,
                "memory": 15,
                "RPM": 0,
                "instances": 4,
                "min_instances": 1,
                "max_instances": 6,
                "healthy": True
            }
        ]
    }
    
    payload = {
        "request": "Review the current services and reduce unnecessary cloud cost without breaking latency or availability requirements.",
        "environment": env
    }
    
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    # Must target reports-worker, NOT analytics-worker
    assert data["identified_service"] == "reports-worker"
    
    report = data["report"]
    # Check that it proposed scale_down
    assert report["final_verification"]["decision"]["proposal"]["action"] == "scale_down"
    # Check that safety approved it
    assert report["final_verification"]["safety_check"]["is_approved"] is True
    # Check that it executed successfully
    assert report["final_verification"]["execution"]["status"] == "SUCCESS"

