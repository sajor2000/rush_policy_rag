from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from app.models.schemas import ChatRequest, ChatResponse, SearchRequest, SearchResponse
import json
from app.dependencies import (
    get_search_index,
    get_on_your_data_service_dep,
    get_cohere_rerank_service,
    get_current_user_claims,
)
from app.services.chat_service import ChatService
from app.core.security import build_applies_to_filter, validate_query
from app.core.rate_limit import limiter  # Shared rate limiter with load balancer support
from app.core.circuit_breaker import azure_openai_breaker, is_circuit_open
from azure_policy_index import PolicySearchIndex
from app.services.on_your_data_service import OnYourDataService
from openai import RateLimitError, APITimeoutError, APIConnectionError
from typing import Optional
import logging
import asyncio
import time
import pybreaker
from app.services.chat_audit_service import get_chat_audit_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/search", response_model=SearchResponse)
@limiter.limit("30/minute")
async def search_policies(
    request: Request,
    body: SearchRequest,
    search_index: PolicySearchIndex = Depends(get_search_index),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Direct search endpoint - returns raw search results.
    """
    if not search_index:
        raise HTTPException(status_code=503, detail="Search index not initialized")

    try:
        validated_query = validate_query(body.query, max_length=500)
        filter_expr = build_applies_to_filter(body.filter_applies_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Wrap synchronous search in thread to avoid blocking event loop
    results = await asyncio.to_thread(
        search_index.search,
        query=validated_query,
        top=body.top,
        filter_expr=filter_expr
    )

    return SearchResponse(
        results=[{
            "citation": r.citation,
            "content": r.content,
            "title": r.title,
            "section": r.section,
            "reference_number": r.reference_number,
            "applies_to": r.applies_to,
            "date_updated": r.date_updated,
            "source_file": r.source_file,
            "document_owner": r.document_owner,
            "date_approved": r.date_approved
        } for r in results],
        query=validated_query,
        count=len(results)
    )

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    search_index: PolicySearchIndex = Depends(get_search_index),
    on_your_data_service: Optional[OnYourDataService] = Depends(get_on_your_data_service_dep),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Process a chat message using Azure OpenAI "On Your Data" (vectorSemanticHybrid).

    Uses vectorSemanticHybrid search for best quality:
    - Vector search (text-embedding-3-large)
    - BM25 + 132 synonym rules
    - L2 Semantic Reranking
    """
    # Start timing for audit
    start_time = time.perf_counter()

    try:
        validated_message = validate_query(body.message, max_length=2000)
        body.message = validated_message
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check circuit breaker before processing
    if is_circuit_open(azure_openai_breaker):
        logger.warning("Circuit breaker open - returning 503")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Service temporarily unavailable. Please try again in a few moments.",
                "retry_after": azure_openai_breaker.reset_timeout
            },
            headers={"Retry-After": str(azure_openai_breaker.reset_timeout)}
        )

    cohere_service = get_cohere_rerank_service()
    service = ChatService(
        search_index,
        on_your_data_service,
        cohere_rerank_service=cohere_service
    )

    try:
        response = await service.process_chat(body)

        # Non-blocking audit logging
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        pipeline = "cohere_rerank" if cohere_service else "on_your_data"
        asyncio.create_task(
            get_chat_audit_service().log_chat(
                request=body,
                response=response,
                latency_ms=latency_ms,
                pipeline_used=pipeline,
                search_query=response.search_query,  # Expanded query for synonym analysis
            )
        )

        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RateLimitError as e:
        # Azure OpenAI rate limit - return 429 with Retry-After header
        retry_after = 60  # Default 60 seconds
        if hasattr(e, 'response') and e.response:
            retry_after = int(e.response.headers.get('Retry-After', 60))
        logger.warning(f"Rate limited by Azure OpenAI: {e}. Retry-After: {retry_after}s")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Too many requests. Please wait before trying again.",
                "retry_after": retry_after
            },
            headers={"Retry-After": str(retry_after)}
        )
    except APITimeoutError as e:
        # Timeout - return 504 Gateway Timeout
        logger.error(f"Azure OpenAI timeout: {e}")
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={
                "detail": "Request timed out. Please try again.",
                "retry_after": 5
            },
            headers={"Retry-After": "5"}
        )
    except APIConnectionError as e:
        # Connection error - return 503 Service Unavailable
        logger.error(f"Azure OpenAI connection error: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Service temporarily unavailable. Please try again.",
                "retry_after": 10
            },
            headers={"Retry-After": "10"}
        )
    except pybreaker.CircuitBreakerError as e:
        # Circuit breaker tripped during request
        logger.warning(f"Circuit breaker tripped: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Service temporarily unavailable. Please try again in a few moments.",
                "retry_after": azure_openai_breaker.reset_timeout
            },
            headers={"Retry-After": str(azure_openai_breaker.reset_timeout)}
        )
    except asyncio.TimeoutError:
        # Internal timeout (from asyncio.wait_for)
        logger.error("Chat processing timed out (asyncio)")
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={
                "detail": "Request timed out. Please try again.",
                "retry_after": 5
            },
            headers={"Retry-After": "5"}
        )
    except Exception as e:
        # Log detailed error server-side but return generic message to client
        logger.error(f"Chat processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your request")


# =============================================================================
# Streaming Chat Endpoint (SSE)
# =============================================================================

def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
@limiter.limit("30/minute")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    search_index: PolicySearchIndex = Depends(get_search_index),
    on_your_data_service: Optional[OnYourDataService] = Depends(get_on_your_data_service_dep),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Stream chat response using Server-Sent Events (SSE).

    Event types:
    - status: Pipeline progress updates ("Searching...", "Generating...")
    - answer_chunk: Partial answer text as it's generated
    - evidence: Evidence items array (sent once at end)
    - sources: Source references (sent once at end)
    - metadata: Response metadata (confidence, chunks_used, found)
    - done: End of stream marker
    - error: Error during streaming
    """
    # Validate input
    try:
        validated_message = validate_query(body.message, max_length=2000)
        body.message = validated_message
    except ValueError as e:
        async def error_gen():
            yield _sse_event("error", {"type": "error", "message": str(e)})
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=400
        )

    # Check circuit breaker
    if is_circuit_open(azure_openai_breaker):
        async def circuit_error_gen():
            yield _sse_event("error", {
                "type": "error",
                "message": "Service temporarily unavailable. Please try again in a few moments.",
                "retry_after": azure_openai_breaker.reset_timeout
            })
        return StreamingResponse(
            circuit_error_gen(),
            media_type="text/event-stream",
            status_code=503
        )

    cohere_service = get_cohere_rerank_service()
    service = ChatService(
        search_index,
        on_your_data_service,
        cohere_rerank_service=cohere_service
    )

    return StreamingResponse(
        service.process_chat_stream(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
