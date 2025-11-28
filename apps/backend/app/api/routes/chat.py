from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.schemas import ChatRequest, ChatResponse, SearchRequest, SearchResponse
from app.dependencies import (
    get_search_index,
    get_on_your_data_service_dep,
    get_current_user_claims,
)
from app.services.chat_service import ChatService
from app.core.security import build_applies_to_filter, validate_query
from app.core.rate_limit import limiter  # Shared rate limiter with load balancer support
from azure_policy_index import PolicySearchIndex
from app.services.on_your_data_service import OnYourDataService
from typing import Optional
import logging
import asyncio

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
    try:
        validated_message = validate_query(body.message, max_length=2000)
        body.message = validated_message
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    service = ChatService(
        search_index,
        on_your_data_service
    )

    try:
        return await service.process_chat(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Log detailed error server-side but return generic message to client
        logger.error(f"Chat processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your request")
