"""
Request logging middleware for FastAPI with App Insights correlation.

Provides:
- Unique request IDs for each request
- Latency tracking
- User/client claim logging (when AAD auth enabled)
- Structured JSON logging compatible with Azure Monitor
"""

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Optional, Callable, Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Context variable for request ID propagation
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs request/response details with correlation IDs.

    Features:
    - Generates unique request ID for each request
    - Logs start/end of requests with latency
    - Extracts user claims from AAD tokens when available
    - Formats logs for App Insights consumption
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate or use existing request ID (from X-Request-ID header)
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        request_id_var.set(request_id)

        # Extract user info from request state (set by auth dependency)
        user_info = self._extract_user_info(request)

        # Log request start
        start_time = time.perf_counter()
        logger.info(
            f"[{request_id}] START {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client_ip": self._get_client_ip(request),
                **user_info,
            }
        )

        # Process request
        try:
            response = await call_next(request)
            latency_ms = (time.perf_counter() - start_time) * 1000

            # Log request completion
            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                f"[{request_id}] END {request.method} {request.url.path} "
                f"status={response.status_code} latency={latency_ms:.1f}ms",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "client_ip": self._get_client_ip(request),
                    **user_info,
                }
            )

            # Add request ID to response headers for client correlation
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"[{request_id}] ERROR {request.method} {request.url.path} "
                f"error={type(e).__name__}: {e} latency={latency_ms:.1f}ms",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "latency_ms": latency_ms,
                    "client_ip": self._get_client_ip(request),
                    **user_info,
                },
                exc_info=True
            )
            raise

    def _extract_user_info(self, request: Request) -> dict:
        """Extract user information from request state (set by AAD auth)."""
        user_info = {}

        # Check if user claims are attached to request state
        if hasattr(request.state, "user_claims") and request.state.user_claims:
            claims = request.state.user_claims
            user_info["user_id"] = claims.get("sub") or claims.get("oid", "")
            user_info["client_id"] = claims.get("azp") or claims.get("appid", "")
            user_info["user_name"] = claims.get("name", "")

        return user_info

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, respecting X-Forwarded-For header."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def get_current_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()


class StructuredLogFormatter(logging.Formatter):
    """
    JSON-structured log formatter for App Insights compatibility.

    Produces logs that App Insights can parse into custom dimensions.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Build base log entry
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields as custom dimensions
        extra_fields = [
            "request_id", "method", "path", "status_code", "latency_ms",
            "client_ip", "user_id", "client_id", "user_name",
            "error_type", "error_message"
        ]

        for field in extra_fields:
            if hasattr(record, field) and getattr(record, field):
                log_entry[field] = getattr(record, field)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        import json
        return json.dumps(log_entry)


def configure_structured_logging(use_json: bool = False):
    """
    Configure logging for structured output.

    Args:
        use_json: If True, use JSON format (for production/App Insights).
                  If False, use human-readable format (for development).
    """
    root_logger = logging.getLogger()

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler()

    if use_json:
        console_handler.setFormatter(StructuredLogFormatter())
    else:
        # Human-readable format for development
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))

    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
