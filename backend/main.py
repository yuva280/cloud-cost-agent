"""Main FastAPI application entrypoint for Cloud Cost Optimization Agent."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.deps import get_state_manager, reset_dependencies
from backend.api.routes.actions import router as actions_router
from backend.api.routes.safety import router as safety_router
from backend.api.routes.services import router as services_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager: seeds default services on startup."""
    reset_dependencies()
    yield


app = FastAPI(
    title="Cloud Cost Optimization Agent API",
    description=(
        "RESTful API providing telemetry, deterministic safety evaluation, "
        "and safe infrastructure action execution for cloud cost optimization."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for local frontend and dashboard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(services_router, prefix="/api")
app.include_router(safety_router, prefix="/api")
app.include_router(actions_router, prefix="/api")


@app.get("/health", tags=["Health"])
@app.get("/api/health", tags=["Health"])
def health_check() -> dict:
    """System health check and state overview."""
    state_mgr = get_state_manager()
    services = state_mgr.list_services()
    return {
        "status": "healthy",
        "service_count": len(services),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": [s.service_id for s in services],
    }


@app.get("/", tags=["Root"])
def root() -> dict:
    """Root endpoint with service metadata."""
    return {
        "name": "Cloud Cost Optimization Agent API",
        "version": "1.0.0",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "endpoints": {
            "services": "/api/services",
            "safety": "/api/safety",
            "actions": "/api/actions",
            "health": "/api/health",
        },
    }
