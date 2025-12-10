"""
Search API Routes - Instance search and within-policy search features.

These endpoints allow users to find specific sections or terms within a policy document,
enabling questions like:
- "show me where 'employee' is mentioned in HIPAA policy"
- "find the section about employee access to their own records"
- "where does it discuss training requirements in the HIPAA policy"
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.schemas import InstanceSearchRequest, InstanceSearchResponse
from app.dependencies import get_search_index
from app.services.instance_search_service import InstanceSearchService
from app.core.rate_limit import limiter
from app.core.security import validate_query
from azure_policy_index import PolicySearchIndex
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search-instances", response_model=InstanceSearchResponse)
@limiter.limit("60/minute")
async def search_instances(
    request: Request,
    body: InstanceSearchRequest,
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """
    Find all instances of a term or relevant sections within a specific policy document.

    This endpoint supports TWO search modes (auto-detected):

    1. EXACT TERM SEARCH (1-2 word queries):
       - Finds every occurrence of the exact term
       - Example: "employee" → all 17 mentions of "employee"

    2. SEMANTIC SECTION SEARCH (longer queries/questions):
       - Finds sections relevant to the topic
       - Example: "employee access to their own records" → relevant sections

    Returns instances with page numbers and context for PDF navigation.
    """
    if not search_index:
        raise HTTPException(status_code=503, detail="Search index not initialized")

    # Validate inputs
    try:
        validated_term = validate_query(body.search_term, max_length=200)
        if len(body.policy_ref) > 50:
            raise ValueError("Policy reference too long")
        if len(body.policy_ref) < 1:
            raise ValueError("Policy reference required")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create service with search client
    service = InstanceSearchService(search_index.get_search_client())

    # Use smart search that auto-detects exact vs semantic mode
    result = await asyncio.to_thread(
        service.search_within_policy,
        policy_ref=body.policy_ref,
        query=validated_term
    )

    logger.info(
        f"Instance search: '{validated_term}' in policy '{body.policy_ref}' "
        f"-> {result.total_instances} results"
    )

    return result


@router.post("/search-within-policy", response_model=InstanceSearchResponse)
@limiter.limit("60/minute")
async def search_within_policy(
    request: Request,
    body: InstanceSearchRequest,
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """
    Alternative endpoint name - same as search-instances.

    Provided for clarity when the intent is to search within a known policy.
    """
    return await search_instances(request, body, search_index)
