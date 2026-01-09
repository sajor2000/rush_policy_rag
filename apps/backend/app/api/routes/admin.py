import os
import secrets
import asyncio
from datetime import date
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Security, Query
from fastapi.security import APIKeyHeader
from app.core.config import settings
from app.dependencies import get_search_index
from app.services.chat_audit_service import get_chat_audit_service
from app.services.cache_service import get_cache_service, invalidate_caches
from app.models.audit_schemas import AuditQueryResponse, AuditStatsResponse
from azure_policy_index import PolicySearchIndex

router = APIRouter()
api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

# Allowed base directories for folder uploads (prevents path traversal)
ALLOWED_BASE_PATHS = [
    Path(settings.BASE_DIR).resolve() if hasattr(settings, 'BASE_DIR') else Path.cwd().resolve(),
    Path("/app/data").resolve(),
    Path.home() / "data",
]


def validate_folder_path(folder_path: str) -> Path:
    """
    Validate that folder_path is within allowed directories.
    Prevents path traversal attacks (e.g., ../../../etc/passwd).
    """
    try:
        abs_path = Path(folder_path).resolve()
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid path: {e}")

    # Check if path is within any allowed base directory
    for allowed_base in ALLOWED_BASE_PATHS:
        try:
            abs_path.relative_to(allowed_base)
            return abs_path  # Path is within this allowed base
        except ValueError:
            continue  # Try next allowed base

    raise HTTPException(
        status_code=400,
        detail="Path outside allowed directories"
    )


async def verify_admin_key(api_key: str = Security(api_key_header)) -> str:
    """Verify admin API key for protected endpoints."""
    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_API_KEY not configured on server"
        )
    # Use constant-time comparison to prevent timing attacks
    if not api_key or not secrets.compare_digest(api_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing admin API key"
        )
    return api_key

@router.get("/index-stats")
async def index_stats(
    _: str = Depends(verify_admin_key),
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """Get search index statistics."""
    return search_index.get_stats()

@router.post("/create-index")
async def create_index(
    _: str = Depends(verify_admin_key),
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """Create or update the search index schema."""
    try:
        search_index.create_index()
        return {"status": "success", "message": "Index created/updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload-folder")
async def upload_folder(
    folder_path: str = Query(..., description="Path to folder with PDFs"),
    _: str = Depends(verify_admin_key),
    search_index: PolicySearchIndex = Depends(get_search_index)
):
    """Process and upload all PDFs in a folder."""
    # Validate path is within allowed directories (prevents path traversal)
    abs_path = validate_folder_path(folder_path)

    if not abs_path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {folder_path}")
    if not abs_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {folder_path}")

    # Import here to avoid circular imports or load heavy deps only when needed
    from preprocessing.chunker import PolicyChunker

    try:
        chunker = PolicyChunker(max_chunk_size=1500)
        # Wrap CPU-intensive sync operation in thread (can take 85-113s for 50 docs)
        result = await asyncio.to_thread(chunker.process_folder, abs_path)

        if result['errors']:
            return {
                "status": "partial",
                "stats": result['stats'],
                "errors": result['errors']
            }

        # Wrap synchronous upload in thread to avoid blocking event loop
        upload_result = await asyncio.to_thread(
            search_index.upload_chunks,
            result['chunks']
        )

        return {
            "status": "success",
            "documents_processed": result['stats']['total_docs'],
            "chunks_created": result['stats']['total_chunks'],
            "chunks_uploaded": upload_result['uploaded'],
            "chunks_failed": upload_result['failed']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Chat Audit Endpoints - RAG Quality Monitoring
# ============================================================================

@router.get("/audit/dates")
async def list_audit_dates(
    limit: int = Query(default=30, ge=1, le=365, description="Max dates to return"),
    _: str = Depends(verify_admin_key),
):
    """List dates with available audit logs (most recent first)."""
    audit_service = get_chat_audit_service()

    if not audit_service.is_enabled:
        raise HTTPException(
            status_code=503,
            detail="Chat audit service is not enabled or configured"
        )

    dates = await audit_service.list_available_dates(limit=limit)
    return {"dates": dates, "count": len(dates)}


@router.get("/audit/records/{audit_date}")
async def get_audit_records(
    audit_date: str,
    limit: int = Query(default=100, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    found: Optional[bool] = Query(default=None, description="Filter by found status"),
    confidence: Optional[str] = Query(default=None, description="Filter by confidence level"),
    needs_human_review: Optional[bool] = Query(default=None, description="Filter by review flag"),
    _: str = Depends(verify_admin_key),
):
    """
    Get audit records for a specific date.

    Date format: YYYY-MM-DD (e.g., 2025-12-23)
    """
    audit_service = get_chat_audit_service()

    if not audit_service.is_enabled:
        raise HTTPException(
            status_code=503,
            detail="Chat audit service is not enabled or configured"
        )

    # Validate date format
    try:
        from datetime import datetime
        datetime.strptime(audit_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD (e.g., 2025-12-23)"
        )

    records, total_count = await audit_service.get_records_for_date(
        date=audit_date,
        limit=limit,
        offset=offset,
        found=found,
        confidence=confidence,
        needs_human_review=needs_human_review,
    )

    return AuditQueryResponse(
        records=records,
        total_count=total_count,
        query_date=audit_date,
    )


@router.get("/audit/stats/{audit_date}")
async def get_audit_stats(
    audit_date: str,
    _: str = Depends(verify_admin_key),
):
    """
    Get aggregated statistics for a specific date.

    Returns: total queries, found rate, confidence breakdown, latency metrics.
    """
    audit_service = get_chat_audit_service()

    if not audit_service.is_enabled:
        raise HTTPException(
            status_code=503,
            detail="Chat audit service is not enabled or configured"
        )

    # Validate date format
    try:
        from datetime import datetime
        datetime.strptime(audit_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD (e.g., 2025-12-23)"
        )

    stats = await audit_service.get_stats_for_date(audit_date)
    return stats


@router.get("/audit/today")
async def get_today_audit(
    limit: int = Query(default=50, ge=1, le=500, description="Max records to return"),
    _: str = Depends(verify_admin_key),
):
    """Get today's audit records (convenience endpoint)."""
    audit_service = get_chat_audit_service()

    if not audit_service.is_enabled:
        raise HTTPException(
            status_code=503,
            detail="Chat audit service is not enabled or configured"
        )

    today = date.today().strftime("%Y-%m-%d")
    records, total = await audit_service.get_records_for_date(today, limit=limit)

    return {
        "date": today,
        "records": records,
        "total_count": total,
    }


# ============================================================================
# Cache Management Endpoints - Response Time Optimization
# ============================================================================

@router.get("/cache/stats")
async def cache_stats(
    _: str = Depends(verify_admin_key),
):
    """
    Get cache statistics for monitoring.

    Returns hit rates, sizes, and memory estimates for all cache layers:
    - Expansion cache: Query synonym expansion
    - Response cache: Full ChatResponse objects
    - Search cache: Azure AI Search results
    """
    cache_service = get_cache_service()
    return cache_service.get_stats()


@router.post("/cache/invalidate")
async def invalidate_cache(
    layer: Optional[str] = Query(
        default=None,
        description="Cache layer to invalidate: 'all', 'response', 'search', or 'expansion'. Default: all."
    ),
    _: str = Depends(verify_admin_key),
):
    """
    Invalidate cache entries.

    Call this after policy updates to ensure fresh data.

    Layers:
    - all: Clear all caches (default)
    - response: Clear only full response cache (24h TTL)
    - search: Clear only search results cache (6h TTL)
    - expansion: Clear only query expansion cache (LRU)
    """
    cache_service = get_cache_service()

    if layer is None or layer == "all":
        counts = cache_service.invalidate_all()
        return {
            "status": "success",
            "message": "All caches invalidated",
            "invalidated": counts
        }
    elif layer == "response":
        count = cache_service.invalidate_responses()
        return {
            "status": "success",
            "message": "Response cache invalidated",
            "invalidated": {"response": count}
        }
    elif layer == "search":
        count = cache_service.invalidate_search()
        return {
            "status": "success",
            "message": "Search cache invalidated",
            "invalidated": {"search": count}
        }
    elif layer == "expansion":
        # Expansion cache doesn't have a dedicated invalidation method
        # Use full invalidation
        counts = invalidate_caches()
        return {
            "status": "success",
            "message": "All caches invalidated (expansion requires full clear)",
            "invalidated": counts
        }
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid layer: {layer}. Use 'all', 'response', 'search', or 'expansion'."
        )


@router.post("/cache/toggle")
async def toggle_cache(
    enabled: bool = Query(..., description="Enable or disable caching"),
    _: str = Depends(verify_admin_key),
):
    """
    Enable or disable the cache service.

    Useful for debugging or when you need to ensure fresh responses.
    """
    cache_service = get_cache_service()
    cache_service.enabled = enabled

    return {
        "status": "success",
        "cache_enabled": enabled,
        "message": f"Cache {'enabled' if enabled else 'disabled'}"
    }
