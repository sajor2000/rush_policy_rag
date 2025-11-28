import os
import secrets
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Security, Query
from fastapi.security import APIKeyHeader
from app.core.config import settings
from app.dependencies import get_search_index
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
