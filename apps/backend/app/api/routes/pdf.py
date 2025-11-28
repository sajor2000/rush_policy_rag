from typing import Optional
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pdf_service import generate_pdf_sas_url, check_pdf_exists
import logging

from app.dependencies import get_current_user_claims

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{filename:path}")
async def get_pdf_url(
    filename: str,
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Generate a secure, time-limited SAS URL for PDF viewing.
    """
    if not filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Wrap sync Azure Storage calls in thread to avoid blocking event loop
    exists = await asyncio.to_thread(check_pdf_exists, filename)
    if not exists:
        raise HTTPException(status_code=404, detail=f"PDF not found: {filename}")

    try:
        result = await asyncio.to_thread(generate_pdf_sas_url, filename)
        return result
    except Exception as e:
        logger.error(f"Error generating PDF URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF URL: {str(e)}")
