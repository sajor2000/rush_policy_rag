from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import ChatRequest, ChatResponse, SearchRequest, SearchResponse
from app.dependencies import get_search_index, get_foundry_client_dep, get_foundry_agent_service_dep
from app.services.chat_service import ChatService
from app.core.security import build_applies_to_filter, validate_query
from azure_policy_index import PolicySearchIndex
from foundry_client import FoundryRAGClient
from app.services.foundry_agent import FoundryAgentService
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/search", response_model=SearchResponse)
async def search_policies(
    request: SearchRequest,
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """
    Direct search endpoint - returns raw search results.
    """
    if not search_index:
        raise HTTPException(status_code=503, detail="Search index not initialized")

    try:
        validated_query = validate_query(request.query, max_length=500)
        filter_expr = build_applies_to_filter(request.filter_applies_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = search_index.search(
        query=validated_query,
        top=request.top,
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
async def chat(
    request: ChatRequest,
    search_index: PolicySearchIndex = Depends(get_search_index),
    foundry_client: Optional[FoundryRAGClient] = Depends(get_foundry_client_dep),
    foundry_agent_service: Optional[FoundryAgentService] = Depends(get_foundry_agent_service_dep)
):
    """
    Process a chat message.
    """
    try:
        validated_message = validate_query(request.message, max_length=2000)
        request.message = validated_message
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    service = ChatService(search_index, foundry_client, foundry_agent_service)
    
    try:
        return await service.process_chat(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Chat processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process request: {str(e)}")
