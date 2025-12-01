"""
FastAPI backend server for RUSH Policy RAG Agent
"""

# Inject system certificate store for corporate proxy support (e.g., Netskope)
# Must be done BEFORE any SSL connections are made
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore not installed, SSL uses default cert handling

import asyncio
import logging
import os
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging_middleware import (
    RequestLoggingMiddleware,
    configure_structured_logging
)
from app.core.rate_limit import limiter  # Shared rate limiter with load balancer support
from app.core.circuit_breaker import get_all_circuit_status
from app.dependencies import lifespan, increment_requests, decrement_requests
from app.api.routes import chat, admin, pdf

# Optional instrumentation - gracefully handle missing dependencies
try:
    from instrumentation import setup_tracing
    _tracing_available = True
except ImportError as e:
    logging.warning(f"Instrumentation module not available: {e}")
    _tracing_available = False
    def setup_tracing(app): pass  # No-op fallback

# Prometheus metrics - gracefully handle missing dependency
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    _prometheus_available = True
except ImportError:
    _prometheus_available = False
    Instrumentator = None

# Configure structured logging (JSON in production, readable in dev)
use_json_logging = os.environ.get("LOG_FORMAT", "text").lower() == "json"
configure_structured_logging(use_json=use_json_logging)
logger = logging.getLogger(__name__)

# Maximum request body size (1MB default, prevents memory exhaustion attacks)
MAX_REQUEST_SIZE = int(os.environ.get("MAX_REQUEST_SIZE", 1 * 1024 * 1024))


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to limit request body size and prevent memory exhaustion."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            if int(content_length) > MAX_REQUEST_SIZE:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "detail": f"Request body too large. Maximum size is {MAX_REQUEST_SIZE // 1024}KB"
                    }
                )
        return await call_next(request)


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware to track active requests for graceful shutdown."""

    async def dispatch(self, request: Request, call_next):
        await increment_requests()
        try:
            return await call_next(request)
        finally:
            await decrement_requests()

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

# Prometheus metrics endpoint
if _prometheus_available and Instrumentator:
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/health", "/metrics"],
    )
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus metrics enabled at /metrics")

# Request tracking middleware (for graceful shutdown)
app.add_middleware(RequestTrackingMiddleware)

# Request size limit middleware (must be early to reject oversized requests)
app.add_middleware(RequestSizeLimitMiddleware)

# Request logging middleware (must be added before CORS)
app.add_middleware(RequestLoggingMiddleware)

# CORS configuration with preflight caching
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=3600,  # Cache preflight requests for 1 hour
)

# Include routers - versioned API (v1 is the canonical version)
app.include_router(chat.router, prefix="/api/v1", tags=["Chat v1"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin v1"])
app.include_router(pdf.router, prefix="/api/v1/pdf", tags=["PDF v1"])

# Legacy routes (deprecated, will be removed in v4.0)
# These maintain backward compatibility with existing frontend deployments
app.include_router(chat.router, prefix="/api", tags=["Chat (deprecated)"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin (deprecated)"])
app.include_router(pdf.router, prefix="/api/pdf", tags=["PDF (deprecated)"])

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

    # Check circuit breakers
    circuit_breakers = get_all_circuit_status()
    # Mark as degraded if any circuit breaker is open
    for cb_name, cb_status in circuit_breakers.items():
        if cb_status.get("state") == "open":
            health_status = "degraded"
            errors.append(f"circuit_breaker_{cb_name}: open")

    # Check blob storage connectivity (for PDF viewing)
    blob_status = {"configured": False}
    try:
        from pdf_service import get_blob_service_client, CONTAINER_NAME
        client = get_blob_service_client()
        container = client.get_container_client(CONTAINER_NAME)
        exists = await asyncio.to_thread(container.exists)
        blob_status = {
            "configured": True,
            "container": CONTAINER_NAME,
            "accessible": exists
        }
        if not exists:
            health_status = "degraded"
            errors.append(f"blob_storage: container '{CONTAINER_NAME}' not found")
    except Exception as e:
        blob_status = {"configured": False, "error": str(e)}
        # Don't mark as degraded - blob storage is optional for core chat functionality
        logger.warning(f"Blob storage health check failed: {e}")

    response_body = {
        "status": health_status,
        "search_index": stats,
        "on_your_data": on_your_data_status,
        "circuit_breakers": circuit_breakers,
        "blob_storage": blob_status,
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
