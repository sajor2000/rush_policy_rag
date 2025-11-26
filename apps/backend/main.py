"""
FastAPI backend server for RUSH Policy RAG Agent
"""

import logging
import uvicorn
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.dependencies import lifespan
from app.api.routes import chat, admin, pdf
from instrumentation import setup_tracing

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RUSH Policy RAG API",
    description="Backend API for RUSH Policy retrieval using Azure AI Foundry with Agentic Retrieval",
    version="3.0.0",
    lifespan=lifespan
)

# Enable observability
setup_tracing(app)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key"],
)

# Include routers
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(pdf.router, prefix="/api/pdf", tags=["PDF"])

@app.get("/health")
async def health_check():
    """Health check endpoint - returns 503 if critical services fail."""
    from app.dependencies import get_search_index, get_foundry_client_dep, get_foundry_agent_service_dep

    health_status = "healthy"
    errors = []

    # Check search index (critical service)
    try:
        search_index = get_search_index()
        stats = search_index.get_stats()
        if isinstance(stats, dict) and "error" in stats:
            health_status = "degraded"
            errors.append(f"search_index: {stats['error']}")
    except RuntimeError as e:
        # RuntimeError indicates critical initialization failure
        health_status = "unhealthy"
        stats = {"error": str(e)}
        errors.append(f"search_index: {e}")
    except Exception as e:
        health_status = "degraded"
        stats = {"error": str(e)}
        errors.append(f"search_index: {e}")

    # Check foundry client (non-critical)
    try:
        foundry_client = get_foundry_client_dep()
        foundry_status = foundry_client.get_status() if foundry_client else {"error": "Not initialized"}
    except Exception as e:
        foundry_status = {"error": str(e)}

    # Check agent service
    try:
        agent_service = get_foundry_agent_service_dep()
        agentic_status = {
            "configured": bool(agent_service),
            "agent_name": agent_service.agent_name if agent_service else None,
            "enabled": settings.USE_AGENTIC_RETRIEVAL,
        }
    except Exception as e:
        agentic_status = {"configured": False, "error": str(e), "enabled": settings.USE_AGENTIC_RETRIEVAL}

    response_body = {
        "status": health_status,
        "search_index": stats,
        "foundry": foundry_status,
        "agentic_retrieval": agentic_status,
        "version": "3.0.0",
    }

    # Only include errors if present
    if errors:
        response_body["errors"] = errors

    # Return 503 if unhealthy (critical service down)
    if health_status == "unhealthy":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response_body
        )

    return response_body

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.BACKEND_PORT,
        reload=True,
        log_level="info"
    )
