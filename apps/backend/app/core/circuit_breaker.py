"""
Circuit Breaker Pattern for Azure Services

Provides resilience against cascading failures by:
- Opening circuit after consecutive failures (fail fast)
- Half-opening after recovery timeout to test if service is back
- Closing circuit when service recovers

Usage:
    from app.core.circuit_breaker import azure_search_breaker, azure_openai_breaker

    with azure_search_breaker:
        result = search_index.search(query)
"""

import logging
from typing import Optional
import pybreaker

logger = logging.getLogger(__name__)


class LoggingCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Log circuit breaker state transitions for observability."""

    def state_change(self, cb: pybreaker.CircuitBreaker, old_state: pybreaker.CircuitBreakerState, new_state: pybreaker.CircuitBreakerState):
        logger.warning(
            f"Circuit breaker '{cb.name}' state changed: {old_state.name} -> {new_state.name}"
        )
        if new_state == pybreaker.STATE_OPEN:
            logger.error(
                f"Circuit breaker '{cb.name}' OPENED after {cb.fail_counter} failures. "
                f"Will retry after {cb.reset_timeout}s"
            )
        elif new_state == pybreaker.STATE_CLOSED:
            logger.info(f"Circuit breaker '{cb.name}' CLOSED - service recovered")

    def failure(self, cb: pybreaker.CircuitBreaker, exc: Exception):
        logger.warning(
            f"Circuit breaker '{cb.name}' recorded failure #{cb.fail_counter}: {exc}"
        )

    def success(self, cb: pybreaker.CircuitBreaker):
        if cb.state == pybreaker.STATE_HALF_OPEN:
            logger.info(f"Circuit breaker '{cb.name}' success in half-open state")


# Shared listener for all circuit breakers
_listener = LoggingCircuitBreakerListener()


# Azure AI Search circuit breaker
# - Opens after 5 consecutive failures
# - Stays open for 30 seconds before trying again
# - Excludes client errors (4xx) from failure count
azure_search_breaker = pybreaker.CircuitBreaker(
    name="azure_search",
    fail_max=5,
    reset_timeout=30,
    listeners=[_listener],
    exclude=[ValueError, KeyError]  # Don't count validation errors
)


# Azure OpenAI circuit breaker
# - Opens after 3 consecutive failures (more sensitive for AI calls)
# - Stays open for 60 seconds (Azure OpenAI rate limits are per-minute)
# - This protects against prolonged outages hammering the service
azure_openai_breaker = pybreaker.CircuitBreaker(
    name="azure_openai",
    fail_max=3,
    reset_timeout=60,
    listeners=[_listener],
    exclude=[ValueError, KeyError]
)


def is_circuit_open(breaker: pybreaker.CircuitBreaker) -> bool:
    """Check if a circuit breaker is currently open."""
    return breaker.state == pybreaker.STATE_OPEN


def get_circuit_status(breaker: pybreaker.CircuitBreaker) -> dict:
    """Get circuit breaker status for health checks."""
    return {
        "name": breaker.name,
        "state": breaker.state.name,
        "fail_counter": breaker.fail_counter,
        "fail_max": breaker.fail_max,
        "reset_timeout": breaker.reset_timeout,
    }


def get_all_circuit_status() -> dict:
    """Get status of all circuit breakers for health endpoint."""
    return {
        "azure_search": get_circuit_status(azure_search_breaker),
        "azure_openai": get_circuit_status(azure_openai_breaker),
    }
