import re
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional, List

from backend.services.state_manager import ServiceStateManager
from backend.schemas.metrics import ServiceObservation

class NLPExtractor:
    """
    Heuristic-based NLP extractor that interprets natural language requests
    and dynamically constructs an initial ServiceObservation from the real ServiceStateManager.
    """
    def __init__(self, state_manager: ServiceStateManager):
        self.state_manager = state_manager

    def extract_intent_and_observation(self, request_text: str) -> Tuple[Optional[str], Optional[ServiceObservation], str]:
        """
        Extracts target service and intent from the natural language text.
        
        Returns:
            Tuple of (service_id, ServiceObservation, interpretation_message)
        """
        text = request_text.lower()
        services = self.state_manager.list_services()
        service_ids = [s.service_id.lower() for s in services]
        
        target_service = None
        
        # 1. Look for explicit service mentions
        for sid in service_ids:
            if sid in text:
                target_service = sid
                break
                
        # 2. Heuristic fallback based on intent if no service is named
        if not target_service:
            if "cost" in text or "reduce" in text or "save" in text or "underutilized" in text or "over-provisioned" in text:
                # Find the most underutilized service safely (lowest CPU)
                if services:
                    underutilized = min(services, key=lambda s: s.cpu_utilization_percent or 100.0)
                    target_service = underutilized.service_id
            elif "traffic" in text or "capacity" in text or "scale up" in text:
                # Find service with highest CPU
                if services:
                    high_load = max(services, key=lambda s: s.cpu_utilization_percent or 0.0)
                    target_service = high_load.service_id

        if not target_service:
            return None, None, "Could not identify a target service or clear intent from the request."

        # 3. Create the REAL observation from the StateManager
        observation = self.state_manager.create_observation(target_service)
        message_parts = [f"Interpreted request targeting real service: '{target_service}'."]

        # 4. Handle specific user-requested scenario overrides
        # (Artificial staleness override removed. Staleness is now strictly evaluated via uploaded JSON timestamps).

        return target_service, observation, " ".join(message_parts)
