"""
Rate limiting configuration for the RUSH Policy RAG API.

This module provides a shared rate limiter that correctly handles
load balancer/proxy scenarios by extracting the real client IP.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_real_client_ip(request: Request) -> str:
    """
    Extract real client IP address, handling load balancer/proxy scenarios.

    Priority:
    1. X-Forwarded-For header (first IP in chain, set by load balancers)
    2. X-Real-IP header (set by some reverse proxies)
    3. Direct client IP (fallback for direct connections)

    This is critical for rate limiting to work correctly behind Azure App Service
    or other load balancers, where all requests would otherwise appear from the
    same internal IP.
    """
    # Check X-Forwarded-For (standard header, may contain chain of proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # First IP is the original client (format: "client, proxy1, proxy2")
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP (used by nginx and some proxies)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Fallback to direct client IP
    return get_remote_address(request)


# Shared rate limiter instance (30 requests per minute per IP)
# Uses custom IP detection to work correctly behind load balancers
limiter = Limiter(key_func=get_real_client_ip, default_limits=["30/minute"])
