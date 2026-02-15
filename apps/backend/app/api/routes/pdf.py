import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_user_claims
from pdf_service import check_pdf_exists, generate_pdf_sas_url

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{filename:path}")
async def get_pdf_url(
    filename: str, _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Generate a secure, time-limited SAS URL for PDF viewing.
    """
    # Path traversal prevention
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Normalize and re-check
    normalized = os.path.normpath(filename)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not normalized.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    filename = normalized

    # Wrap sync Azure Storage calls in thread to avoid blocking event loop
    exists = await asyncio.to_thread(check_pdf_exists, filename)
    if not exists:
        raise HTTPException(status_code=404, detail="PDF not found")

    try:
        result = await asyncio.to_thread(generate_pdf_sas_url, filename)
        return result
    except Exception as e:
        logger.error(f"Error generating PDF URL: {e}")
        raise HTTPException(status_code=500, detail="Unable to access PDF document")
