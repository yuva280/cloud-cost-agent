from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

from backend.schemas.workflow import WorkflowReport
from backend.schemas.metrics import ServiceObservation
from backend.schemas.service_state import ServiceState
from backend.orchestrator.orchestrator import Orchestrator
from backend.api.deps import get_state_manager, get_execution_engine
from backend.agents.nlp_parser import NLPExtractor

router = APIRouter()

def ensure_demo_services(state_mgr):
    if not state_mgr.service_exists("checkout-api"):
        state_mgr.register_service(ServiceState(
            service_id="checkout-api",
            current_instances=5,
            min_instances=2,
            max_instances=8,
            latency_ms=170.0,
            max_latency_ms=250.0,
            traffic_rpm=900,
            healthy=True,
            state_version="v1",
            cpu_utilization_percent=24.0,
            memory_utilization_percent=39.0,
            cost_per_hour=4.5,
            service_type="web",
            is_critical=True,
        ))
    if not state_mgr.service_exists("payment-api"):
        state_mgr.register_service(ServiceState(
            service_id="payment-api",
            current_instances=3,
            min_instances=2,
            max_instances=8,
            latency_ms=410.0,
            max_latency_ms=300.0,
            traffic_rpm=6400,
            healthy=True,
            state_version="v4",
            cpu_utilization_percent=91.0,
            memory_utilization_percent=82.0,
            cost_per_hour=8.2,
            service_type="worker",
            is_critical=True,
        ))
    if not state_mgr.service_exists("reporting-job"):
        state_mgr.register_service(ServiceState(
            service_id="reporting-job",
            current_instances=4,
            min_instances=1,
            max_instances=4,
            latency_ms=45.0,
            max_latency_ms=500.0,
            traffic_rpm=120,
            healthy=True,
            state_version="v2",
            cpu_utilization_percent=8.0,
            memory_utilization_percent=12.0,
            cost_per_hour=12.0,
            service_type="batch",
            is_critical=False,
        ))

class AgentRequest(BaseModel):
    request: str
    environment: Optional[Dict[str, Any]] = None

class AgentResponse(BaseModel):
    original_request: str
    identified_service: Optional[str]
    message: str
    report: WorkflowReport

from backend.services.execution_service import ExecutionService
from backend.schemas.actions import InfrastructureAction
from backend.api.environment_parser import EnvironmentParser

@router.post("/run", response_model=AgentResponse)
def run_agent(payload: AgentRequest) -> AgentResponse:
    text = payload.request.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Request cannot be empty.")
    
    state_mgr = get_state_manager()
    
    # 1. Parse authoritative JSON environment if provided
    explicit_obs = None
    simulated_failure = None
    if payload.environment is not None:
        if not payload.environment:
            raise HTTPException(status_code=400, detail="Uploaded environment is empty or invalid.")
        state_mgr.reset()
        parser = EnvironmentParser(state_manager=state_mgr)
        explicit_obs, simulated_failure = parser.parse(payload.environment)
    else:
        # Fallback to demo service injection ONLY if no JSON was uploaded
        ensure_demo_services(state_mgr)
    
    # 2. Extract intent and construct observation
    extractor = NLPExtractor(state_manager=state_mgr)
    target_service, observation, message = extractor.extract_intent_and_observation(text)
    
    # If the JSON explicitly provided an observation (e.g. Test C stale observation), override it!
    if explicit_obs and payload.environment:
        observation = explicit_obs
        target_service = observation.service_id
        message = f"Authoritative Observation loaded from JSON for '{target_service}'."
    elif payload.environment is not None:
        # Environment was uploaded but no explicit observation override.
        # The NLP extractor ran against the state manager which was reset and re-populated
        # with only the uploaded services. Verify the selected service actually exists
        # in the uploaded environment; if not, select from uploaded services directly.
        env_service_ids = state_mgr.list_service_ids()
        if target_service and target_service in env_service_ids:
            # NLP correctly selected a service from the uploaded environment
            message = f"Interpreted request targeting uploaded service: '{target_service}'."
        elif env_service_ids:
            # NLP selected a service not in the uploaded environment, or failed.
            # Select the most underutilized service from the uploaded environment.
            env_services = state_mgr.list_services()
            best = min(env_services, key=lambda s: s.cpu_utilization_percent or 100.0)
            target_service = best.service_id
            observation = state_mgr.create_observation(target_service)
            message = f"Interpreted request targeting uploaded service: '{target_service}'."
        
    if not target_service or not observation:
        raise HTTPException(status_code=400, detail=message)

    # 3. Configure ExecutionService
    # If a failure was provided in JSON, configure a dedicated ExecutionService instance
    exec_svc = ExecutionService()
    if simulated_failure:
        # Map string to enum safely
        action_str = simulated_failure["action"].upper()
        action_enum = getattr(InfrastructureAction, action_str, None)
        if action_enum:
            exec_svc.register_failure(
                service_id=simulated_failure["target"],
                action=action_enum,
                error_code=simulated_failure["error_code"],
                error_message=simulated_failure["error_message"]
            )

    orchestrator = Orchestrator(executor=exec_svc.execute)
    
    # 4. Run orchestrator
    report = orchestrator.run(observation)
    
    report_dict = report.model_dump(mode="json")
    if report_dict.get("final_verification") and report_dict["final_verification"].get("execution"):
        status_val = report_dict["final_verification"]["execution"].get("status")
        if isinstance(status_val, str):
            report_dict["final_verification"]["execution"]["status"] = status_val.upper()
    
    from fastapi.responses import JSONResponse
    return JSONResponse(content={
        "original_request": text,
        "identified_service": target_service,
        "message": message,
        "report": report_dict
    })
