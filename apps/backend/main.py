"""
FastAPI backend server for RUSH Policy RAG Agent
"""

import logging
import os
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging_middleware import (
    RequestLoggingMiddleware,
    configure_structured_logging
)
from app.core.rate_limit import limiter  # Shared rate limiter with load balancer support
from app.dependencies import lifespan
from app.api.routes import chat, admin, pdf

# Optional instrumentation - gracefully handle missing dependencies
try:
    from instrumentation import setup_tracing
    _tracing_available = True
except ImportError as e:
    logging.warning(f"Instrumentation module not available: {e}")
    _tracing_available = False
    def setup_tracing(app): pass  # No-op fallback

# Configure structured logging (JSON in production, readable in dev)
use_json_logging = os.environ.get("LOG_FORMAT", "text").lower() == "json"
configure_structured_logging(use_json=use_json_logging)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RUSH Policy RAG API",
    description="Backend API for RUSH Policy retrieval using Azure OpenAI On Your Data",
    version="3.0.0",
    lifespan=lifespan
)

# Rate limiting setup
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Enable observability
setup_tracing(app)

# Request logging middleware (must be added before CORS)
app.add_middleware(RequestLoggingMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

# Include routers
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(pdf.router, prefix="/api/pdf", tags=["PDF"])

@app.get("/health")
async def health_check():
    """Health check endpoint - returns 503 if critical services fail."""
    from app.dependencies import get_search_index, get_on_your_data_service_dep

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

    # Check On Your Data service (vectorSemanticHybrid)
    try:
        on_your_data_service = get_on_your_data_service_dep()
        on_your_data_status = {
            "configured": bool(on_your_data_service and on_your_data_service.is_configured),
            "query_type": "vectorSemanticHybrid" if on_your_data_service else None,
            "semantic_config": on_your_data_service.semantic_config if on_your_data_service else None,
            "enabled": settings.USE_ON_YOUR_DATA,
        }
    except Exception as e:
        on_your_data_status = {"configured": False, "error": str(e), "enabled": settings.USE_ON_YOUR_DATA}

    response_body = {
        "status": health_status,
        "search_index": stats,
        "on_your_data": on_your_data_status,
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
