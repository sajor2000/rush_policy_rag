"""
OpenTelemetry Tracing Configuration for RUSH Policy RAG Agent

Supports two modes:
1. Foundry Mode: Uses AIProjectClient to get App Insights connection string
2. Fallback Mode: Uses APPLICATIONINSIGHTS_CONNECTION_STRING environment variable

Instruments FastAPI, OpenAI client, and Azure AI Inference (when available).
"""

import os
import logging
from typing import Optional
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_tracing_initialized = False


def setup_tracing(app: FastAPI, foundry_client=None):
    """
    Configure OpenTelemetry tracing with Azure Monitor.

    Args:
        app: FastAPI application instance
        foundry_client: Optional FoundryRAGClient for Foundry mode

    Tracing is enabled if either:
    - APPLICATIONINSIGHTS_CONNECTION_STRING env var is set, OR
    - foundry_client is provided and can retrieve connection string
    """
    global _tracing_initialized

    if _tracing_initialized:
        logger.debug("Tracing already initialized, skipping")
        return

    connection_string = None

    # Try to get connection string from Foundry client first
    if foundry_client:
        try:
            connection_string = foundry_client.get_application_insights_connection_string()
            if connection_string:
                logger.info("Using App Insights connection string from Foundry")
        except Exception as e:
            logger.warning(f"Failed to get App Insights from Foundry: {e}")

    # Fallback to environment variable
    if not connection_string:
        connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")

    if not connection_string:
        logger.warning("No Application Insights connection string found. Tracing disabled.")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.openai import OpenAIInstrumentor

        # Configure Azure Monitor
        configure_azure_monitor(connection_string=connection_string)

        # Instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)

        # Instrument OpenAI SDK
        OpenAIInstrumentor().instrument()

        # Try to instrument Azure AI Inference (Foundry SDK)
        try:
            from azure.ai.inference.tracing import AIInferenceInstrumentor
            AIInferenceInstrumentor().instrument()
            logger.info("Azure AI Inference instrumentation enabled")
        except ImportError:
            logger.debug("azure-ai-inference not available, skipping AI Inference instrumentation")
        except Exception as e:
            logger.warning(f"Failed to instrument AI Inference: {e}")

        _tracing_initialized = True
        logger.info("Azure Monitor OpenTelemetry tracing enabled")

    except ImportError as e:
        logger.error(f"Missing tracing dependencies: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")


def get_tracing_status() -> dict:
    """Get current tracing status for health checks."""
    return {
        "initialized": _tracing_initialized,
        "connection_string_configured": bool(
            os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        ),
    }
