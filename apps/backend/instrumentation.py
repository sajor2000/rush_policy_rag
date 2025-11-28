"""
OpenTelemetry Tracing Configuration for RUSH Policy RAG Agent

Uses APPLICATIONINSIGHTS_CONNECTION_STRING environment variable for Azure Monitor integration.
Instruments FastAPI, OpenAI client, and Azure AI Inference (when available).
"""

import os
import logging
from typing import Optional
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_tracing_initialized = False


def setup_tracing(app: FastAPI):
    """
    Configure OpenTelemetry tracing with Azure Monitor.

    Args:
        app: FastAPI application instance

    Tracing is enabled if APPLICATIONINSIGHTS_CONNECTION_STRING env var is set.
    """
    global _tracing_initialized

    if _tracing_initialized:
        logger.debug("Tracing already initialized, skipping")
        return

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

        # Try to instrument Azure AI Inference
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
