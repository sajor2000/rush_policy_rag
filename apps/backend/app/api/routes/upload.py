"""
PDF Upload API Routes

Endpoints for uploading PDFs and tracking processing status.
"""

from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
import logging

from app.services.upload_service import get_upload_service, UploadService, JobStatus
from app.dependencies import get_current_user_claims

logger = logging.getLogger(__name__)

router = APIRouter()


# Response Models
class UploadResponse(BaseModel):
    """Response from upload initiation."""
    job_id: str
    filename: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response for job status query."""
    job_id: str
    filename: str
    status: str
    progress: int
    chunks_created: int
    error: Optional[str] = None
    created_at: str
    updated_at: str


class JobListResponse(BaseModel):
    """Response for job listing."""
    jobs: List[JobStatusResponse]
    count: int


def get_upload_service_dep() -> UploadService:
    """Dependency to get upload service."""
    return get_upload_service()


@router.post("", response_model=UploadResponse)
async def upload_pdf(
    file: UploadFile = File(..., description="PDF file to upload"),
    upload_service: UploadService = Depends(get_upload_service_dep),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Upload a PDF file for processing and indexing.

    The file is validated, uploaded to Azure Blob Storage, and queued for
    processing. Processing happens asynchronously - use the status endpoint
    to track progress.

    Returns:
        UploadResponse with job_id for status tracking
    """
    if not upload_service.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Upload service not configured. Contact administrator."
        )

    # Validate content type
    if file.content_type and file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Only PDF files are supported."
        )

    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded file: {e}")
        raise HTTPException(
            status_code=400,
            detail="Failed to read uploaded file"
        )

    # Start upload process
    try:
        job = await upload_service.start_upload(file.filename, content)
        return UploadResponse(
            job_id=job.job_id,
            filename=job.filename,
            status=job.status.value,
            message=f"Upload started. Track progress at /api/upload/status/{job.job_id}"
        )
    except ValueError as e:
        # Validation errors (file type, size, etc.)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Configuration errors
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to process upload. Please try again."
        )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_upload_status(
    job_id: str,
    upload_service: UploadService = Depends(get_upload_service_dep),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    Get the status of an upload job.

    Status values:
    - queued: Job created, waiting to start
    - uploading: Uploading to blob storage
    - processing: Processing PDF (chunking, embedding)
    - indexing: Uploading to search index
    - completed: Successfully processed
    - failed: Processing failed (check error field)
    """
    job = upload_service.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job not found: {job_id}"
        )

    return JobStatusResponse(
        job_id=job.job_id,
        filename=job.filename,
        status=job.status.value,
        progress=job.progress,
        chunks_created=job.chunks_created,
        error=job.error,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat()
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_upload_jobs(
    limit: int = 20,
    upload_service: UploadService = Depends(get_upload_service_dep),
    _: Optional[dict] = Depends(get_current_user_claims)
):
    """
    List recent upload jobs.

    Jobs are retained for 24 hours after creation.
    """
    jobs = upload_service.list_jobs(limit=limit)

    return JobListResponse(
        jobs=[
            JobStatusResponse(
                job_id=job.job_id,
                filename=job.filename,
                status=job.status.value,
                progress=job.progress,
                chunks_created=job.chunks_created,
                error=job.error,
                created_at=job.created_at.isoformat(),
                updated_at=job.updated_at.isoformat()
            )
            for job in jobs
        ],
        count=len(jobs)
    )
