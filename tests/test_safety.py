"""Unit tests for the Deterministic Safety Engine / Service."""

import pytest
from datetime import datetime, timezone, timedelta

from backend.schemas.actions import ActionProposal, InfrastructureAction
from backend.schemas.metrics import ServiceObservation
from backend.services.safety_service import SafetyEngine, SafetyService


def create_observation(
    service_id: str = "test-service",
    cpu: float = 50.0,
    mem: float = 50.0,
    traffic: int = 3000,
    latency: float = 100.0,
    cost: float = 20.0,
    age_seconds: float = 0,
    version: str = "v1",
) -> ServiceObservation:
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return ServiceObservation(
        service_id=service_id,
        cpu_utilization_percent=cpu,
        memory_utilization_percent=mem,
        traffic_rpm=traffic,
        latency_ms=latency,
        cost_per_hour=cost,
        observation_timestamp=timestamp,
        state_version=version,
    )


def create_proposal(
    action: InfrastructureAction,
    target_service_id: str = "test-service",
    version: str = "v1",
    confidence: float = 1.0,
) -> ActionProposal:
    return ActionProposal(
        action=action,
        target_service_id=target_service_id,
        reason="Test proposal reason",
        expected_effect="Test expected effect",
        observation_version=version,
        confidence=confidence,
    )


class TestSafetyEngine:
    def test_matching_fresh_scale_down_approved(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=10.0, mem=10.0, traffic=500, latency=50.0)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is True
        assert res.proposal_version == "v1"
        assert res.evaluated_against_version == obs.state_version
        assert len(res.rejection_reasons) == 0

    def test_scale_down_with_high_cpu_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=85.0, mem=10.0, traffic=500, latency=50.0)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("CPU utilization is high" in r for r in res.rejection_reasons)

    def test_scale_down_with_high_memory_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=10.0, mem=85.0, traffic=500, latency=50.0)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Memory utilization is high" in r for r in res.rejection_reasons)

    def test_scale_down_with_high_traffic_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=10.0, mem=10.0, traffic=6000, latency=50.0)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Traffic RPM is high" in r for r in res.rejection_reasons)

    def test_scale_down_with_high_latency_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=10.0, mem=10.0, traffic=500, latency=400.0)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Latency is high" in r for r in res.rejection_reasons)

    def test_scale_up_approved_under_load(self):
        engine = SafetyEngine()
        obs = create_observation(cpu=91.0, mem=82.0, traffic=6400, latency=410.0)
        prop = create_proposal(InfrastructureAction.SCALE_UP)
        res = engine.check(prop, obs)

        assert res.is_approved is True
        assert len(res.rejection_reasons) == 0

    def test_stale_scale_down_rejected(self):
        engine = SafetyEngine(staleness_threshold_seconds=300)
        obs = create_observation(cpu=10.0, mem=10.0, age_seconds=600)
        prop = create_proposal(InfrastructureAction.SCALE_DOWN)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Stale observation safety" in r for r in res.rejection_reasons)

    def test_stale_scale_up_rejected(self):
        engine = SafetyEngine(staleness_threshold_seconds=300)
        obs = create_observation(cpu=91.0, mem=82.0, age_seconds=600)
        prop = create_proposal(InfrastructureAction.SCALE_UP)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Stale observation safety" in r for r in res.rejection_reasons)

    def test_stale_no_action_approved(self):
        engine = SafetyEngine(staleness_threshold_seconds=300)
        obs = create_observation(cpu=91.0, mem=82.0, age_seconds=600)
        prop = create_proposal(InfrastructureAction.NO_ACTION)
        res = engine.check(prop, obs)

        assert res.is_approved is True
        assert len(res.rejection_reasons) == 0

    def test_version_mismatch_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(version="v2")
        prop = create_proposal(InfrastructureAction.SCALE_UP, version="v1")
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Observation version mismatch" in r for r in res.rejection_reasons)

    def test_matching_no_action_approved(self):
        engine = SafetyEngine()
        obs = create_observation(version="v1")
        prop = create_proposal(InfrastructureAction.NO_ACTION, version="v1")
        res = engine.check(prop, obs)

        assert res.is_approved is True
        assert res.evaluated_against_version == obs.state_version

    def test_target_service_mismatch_rejected(self):
        engine = SafetyEngine()
        obs = create_observation(service_id="payment-service")
        prop = create_proposal(InfrastructureAction.SCALE_UP, target_service_id="auth-service")
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Target service mismatch" in r for r in res.rejection_reasons)

    def test_stop_idle_service_approved_when_idle(self):
        engine = SafetyEngine()
        obs = create_observation(traffic=0, cpu=1.0, mem=4.0)
        prop = create_proposal(InfrastructureAction.STOP_IDLE_SERVICE)
        res = engine.check(prop, obs)

        assert res.is_approved is True
        assert len(res.rejection_reasons) == 0

    def test_stop_idle_service_rejected_when_traffic_present(self):
        engine = SafetyEngine()
        obs = create_observation(traffic=150, cpu=1.0, mem=4.0)
        prop = create_proposal(InfrastructureAction.STOP_IDLE_SERVICE)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Traffic RPM is greater than 0" in r for r in res.rejection_reasons)

    def test_stop_idle_service_rejected_when_active_cpu(self):
        engine = SafetyEngine()
        obs = create_observation(traffic=0, cpu=25.0, mem=4.0)
        prop = create_proposal(InfrastructureAction.STOP_IDLE_SERVICE)
        res = engine.check(prop, obs)

        assert res.is_approved is False
        assert any("Service is not idle" in r for r in res.rejection_reasons)

    def test_alias_compatibility(self):
        service = SafetyService()
        obs = create_observation()
        prop = create_proposal(InfrastructureAction.NO_ACTION)
        res = service.check(prop, obs)
        assert res.is_approved is True
