import pytest
from datetime import datetime, timezone, timedelta
from backend.schemas.metrics import ServiceObservation
from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.agents.safety_engine import SafetyEngine

def create_observation(
    cpu=50.0, mem=50.0, traffic=3000, latency=100.0, age_seconds=0, version="v1"
) -> ServiceObservation:
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return ServiceObservation(
        service_id="test-service",
        cpu_utilization_percent=cpu,
        memory_utilization_percent=mem,
        traffic_rpm=traffic,
        latency_ms=latency,
        cost_per_hour=20.0,
        observation_timestamp=timestamp,
        state_version=version
    )

def create_proposal(action: InfrastructureAction, version="v1") -> ActionProposal:
    return ActionProposal(
        action=action,
        target_service_id="test-service",
        reason="Test reason",
        expected_effect="Test effect",
        observation_version=version,
        confidence=1.0
    )

def test_matching_fresh_scale_down_approved():
    engine = SafetyEngine()
    obs = create_observation(cpu=10.0, mem=10.0, traffic=500, latency=50.0)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is True
    assert res.evaluated_against_version == obs.state_version

def test_scale_down_with_cpu_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=85.0, mem=10.0, traffic=500, latency=50.0)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("CPU utilization is high" in reason for reason in res.rejection_reasons)

def test_scale_down_with_memory_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=10.0, mem=85.0, traffic=500, latency=50.0)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Memory utilization is high" in reason for reason in res.rejection_reasons)

def test_scale_down_with_traffic_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=10.0, mem=10.0, traffic=6000, latency=50.0)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Traffic RPM is high" in reason for reason in res.rejection_reasons)

def test_scale_down_with_latency_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=10.0, mem=10.0, traffic=500, latency=400.0)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Latency is high" in reason for reason in res.rejection_reasons)

def test_test_d_scale_up_approved():
    engine = SafetyEngine()
    obs = create_observation(cpu=91.0, mem=82.0, traffic=6400, latency=410.0)
    prop = create_proposal(InfrastructureAction.SCALE_UP)
    res = engine.check(prop, obs)
    assert res.is_approved is True
    assert len(res.rejection_reasons) == 0

def test_stale_scale_down_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=10.0, mem=10.0, age_seconds=600)
    prop = create_proposal(InfrastructureAction.SCALE_DOWN)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Stale observation safety" in reason for reason in res.rejection_reasons)

def test_stale_scale_up_rejected():
    engine = SafetyEngine()
    obs = create_observation(cpu=91.0, mem=82.0, age_seconds=600)
    prop = create_proposal(InfrastructureAction.SCALE_UP)
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Stale observation safety" in reason for reason in res.rejection_reasons)

def test_stale_no_action_approved():
    engine = SafetyEngine()
    obs = create_observation(cpu=91.0, mem=82.0, age_seconds=600)
    prop = create_proposal(InfrastructureAction.NO_ACTION)
    res = engine.check(prop, obs)
    assert res.is_approved is True

def test_version_mismatch_rejected():
    engine = SafetyEngine()
    obs = create_observation(version="v2")
    prop = create_proposal(InfrastructureAction.SCALE_UP, version="v1")
    res = engine.check(prop, obs)
    assert res.is_approved is False
    assert any("Observation version mismatch" in reason for reason in res.rejection_reasons)

def test_matching_no_action_approved():
    engine = SafetyEngine()
    obs = create_observation(version="v1")
    prop = create_proposal(InfrastructureAction.NO_ACTION, version="v1")
    res = engine.check(prop, obs)
    assert res.is_approved is True
    assert res.evaluated_against_version == obs.state_version
def test_future_timestamp_rejected():
    engine = SafetyEngine()

    future_timestamp = datetime.now(timezone.utc) + timedelta(seconds=300)

    obs = ServiceObservation(
        service_id="test-service",
        cpu_utilization_percent=91.0,
        memory_utilization_percent=82.0,
        traffic_rpm=6400,
        latency_ms=410.0,
        cost_per_hour=20.0,
        observation_timestamp=future_timestamp,
        state_version="v1",
    )

    prop = create_proposal(InfrastructureAction.SCALE_UP)

    res = engine.check(prop, obs)

    assert res.is_approved is False
    assert any(
        "Future timestamp safety" in reason
        for reason in res.rejection_reasons
    )

