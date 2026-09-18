import { Scenario, WorkflowReport, ServiceState } from '../types/schemas';

export const getInitialStates = (): Record<string, ServiceState> => ({
  "checkout-api": {
    current_instances: 5,
    min_instances: 2,
    max_instances: 8,
    latency_ms: 170,
    max_latency_ms: 250,
    traffic_rpm: 900,
    healthy: true,
    state_version: "v1",
    cpu_utilization_percent: 24,
    memory_utilization_percent: 39,
    cost_per_hour: 4.5,
    service_type: "web",
    is_critical: true
  },
  "payment-api": {
    current_instances: 3,
    min_instances: 2,
    max_instances: 8,
    latency_ms: 410,
    max_latency_ms: 300,
    traffic_rpm: 6400,
    healthy: true,
    state_version: "v4",
    cpu_utilization_percent: 91,
    memory_utilization_percent: 82,
    cost_per_hour: 8.2,
    service_type: "worker",
    is_critical: true
  },
  "reporting-job": {
    current_instances: 4,
    min_instances: 1,
    max_instances: 4,
    latency_ms: 45,
    max_latency_ms: 500,
    traffic_rpm: 120,
    healthy: true,
    state_version: "v2",
    cpu_utilization_percent: 8,
    memory_utilization_percent: 12,
    cost_per_hour: 12.0,
    service_type: "batch",
    is_critical: false
  }
});

const staleObservationTimestamp = new Date(Date.now() - 3600000).toISOString(); // 1 hour ago
const currentTimestamp = new Date().toISOString();

export const scenarios: Scenario[] = [
  {
    id: "stale-observation",
    name: "Scenario 1: Rising Traffic / Stale Observation",
    description: "Agent receives an old observation with low traffic, but the actual traffic is currently spiking. It correctly detects staleness and rejects the unsafe scale-down.",
    initialState: {
      ...getInitialStates(),
      "checkout-api": {
        ...getInitialStates()["checkout-api"],
        traffic_rpm: 5200 // the current real traffic spiking
      }
    },
    workflow: {
      workflow_id: "wf-1001",
      initial_observation: {
        service_id: "checkout-api",
        cpu_utilization_percent: 24,
        memory_utilization_percent: 39,
        traffic_rpm: 900,
        latency_ms: 170,
        cost_per_hour: 4.5,
        observation_timestamp: staleObservationTimestamp,
        state_version: "v1"
      },
      final_verification: {
        decision: {
          investigation: {
            observation: {
              service_id: "checkout-api",
              cpu_utilization_percent: 24,
              memory_utilization_percent: 39,
              traffic_rpm: 900,
              latency_ms: 170,
              cost_per_hour: 4.5,
              observation_timestamp: staleObservationTimestamp,
              state_version: "v1"
            },
            identified_issues: ["Potentially stale observation: Timestamp is older than threshold.", "Underutilization detected based on stale metrics."],
            summary: "WARNING: Observation is stale and should not be used to confidently scale down."
          },
          proposal: {
            action: "scale_down",
            target_service_id: "checkout-api",
            reason: "CPU is at 24% (but data is stale). Proposing scale down with low confidence.",
            expected_effect: "Reduce cost to 2.5/hr. Risk of traffic impact.",
            observation_version: "v1",
            confidence: 0.2
          }
        },
        safety_check: {
            is_approved: false,
            proposal_version: "v1",
            evaluated_against_version: "v1",
            rejection_reasons: [
                "Stale observation safety: observation is 3600s old, which exceeds the threshold of 300s."
            ],
            applied_rules: ["staleness_safety"]
        },
        execution: null,
        is_successful: false,
        verification_notes: "Execution was not authorized by the Safety Engine. No infrastructure changes were made."
      }
    }
  },
  {
    id: "failed-scale-up",
    name: "Scenario 2: Failed Scale Up (Capacity Unavailable)",
    description: "Agent detects severe load on payment-api and decides to scale up, but cloud provider rejects due to capacity limits. Verification handles the failure.",
    initialState: getInitialStates(),
    workflow: {
      workflow_id: "wf-1002",
      initial_observation: {
        service_id: "payment-api",
        cpu_utilization_percent: 91,
        memory_utilization_percent: 82,
        traffic_rpm: 6400,
        latency_ms: 410,
        cost_per_hour: 8.2,
        observation_timestamp: currentTimestamp,
        state_version: "v4"
      },
      final_verification: {
        decision: {
          investigation: {
            observation: {
                service_id: "payment-api",
                cpu_utilization_percent: 91,
                memory_utilization_percent: 82,
                traffic_rpm: 6400,
                latency_ms: 410,
                cost_per_hour: 8.2,
                observation_timestamp: currentTimestamp,
                state_version: "v4"
            },
            identified_issues: ["High CPU pressure (>80%)", "High Memory pressure (>80%)", "Latency violation (410ms > 300ms max)"],
            summary: "The payment-api is experiencing severe scale/health pressure. Immediate scale up is required."
          },
          proposal: {
            action: "scale_up",
            target_service_id: "payment-api",
            reason: "CPU and memory are critically high, latency violates SLA. Need more instances.",
            expected_effect: "Reduce CPU to ~60%, lower latency to <200ms, increase cost.",
            observation_version: "v4",
            confidence: 0.95
          }
        },
        safety_check: {
            is_approved: true,
            proposal_version: "v4",
            evaluated_against_version: "v4",
            rejection_reasons: [],
            applied_rules: ["service_target_safety", "observation_version_safety"]
        },
        execution: {
            action: "scale_up",
            target_service_id: "payment-api",
            status: "FAILURE",
            error_code: "capacity_unavailable",
            error_message: "Host cluster capacity unavailable for payment-api scale_up from 3 to 5 instances.",
            new_state_version: null
        },
        is_successful: false,
        verification_notes: "Execution failed (Code: capacity_unavailable) for action 'scale_up' on service 'payment-api'. No state version changed."
      }
    }
  },
  {
    id: "safe-optimization",
    name: "Scenario 3: Safe Optimization (Scale Down)",
    description: "Agent correctly identifies an over-provisioned batch reporting job safely executing a scale down operation to reduce costs.",
    initialState: getInitialStates(),
    workflow: {
      workflow_id: "wf-1003",
      initial_observation: {
        service_id: "reporting-job",
        cpu_utilization_percent: 8,
        memory_utilization_percent: 12,
        traffic_rpm: 120,
        latency_ms: 45,
        cost_per_hour: 12.0,
        observation_timestamp: currentTimestamp,
        state_version: "v2"
      },
      final_verification: {
        decision: {
          investigation: {
            observation: {
                service_id: "reporting-job",
                cpu_utilization_percent: 8,
                memory_utilization_percent: 12,
                traffic_rpm: 120,
                latency_ms: 45,
                cost_per_hour: 12.0,
                observation_timestamp: currentTimestamp,
                state_version: "v2"
            },
            identified_issues: ["Severe underutilization (CPU: 8%, Mem: 12%)", "High cost opportunity"],
            summary: "The reporting-job is highly over-provisioned relative to its current traffic. A safe scale down will yield cost savings."
          },
          proposal: {
            action: "scale_down",
            target_service_id: "reporting-job",
            reason: "Sustained low utilization indicates excess capacity.",
            expected_effect: "Increase CPU to ~30%, reduce cost to 6.0/hr without impacting SLA.",
            observation_version: "v2",
            confidence: 0.90
          }
        },
        safety_check: {
            is_approved: true,
            proposal_version: "v2",
            evaluated_against_version: "v2",
            rejection_reasons: [],
            applied_rules: ["service_target_safety", "observation_version_safety", "scale_down_protection"]
        },
        execution: {
            action: "scale_down",
            target_service_id: "reporting-job",
            status: "SUCCESS",
            error_code: null,
            error_message: null,
            new_state_version: "v3"
        },
        is_successful: true,
        verification_notes: "Successfully executed action 'scale_down' on target service 'reporting-job'. New state version reported by execution layer: v3. Post-execution observation confirms the state version changed to v3."
      }
    }
  }
];
