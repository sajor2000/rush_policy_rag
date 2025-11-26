from fastapi import APIRouter, HTTPException
from pdf_service import generate_pdf_sas_url, check_pdf_exists
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{filename:path}")
async def get_pdf_url(filename: str):
    """
    Generate a secure, time-limited SAS URL for PDF viewing.
    """
    if not filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    if not check_pdf_exists(filename):
        raise HTTPException(status_code=404, detail=f"PDF not found: {filename}")

    try:
        result = generate_pdf_sas_url(filename)
        return result
    except Exception as e:
        logger.error(f"Error generating PDF URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF URL: {str(e)}")
