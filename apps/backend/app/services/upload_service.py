"""
PDF Upload Service - Handles file upload, processing, and indexing.

This service manages the complete PDF upload workflow:
1. Validate uploaded PDF files
2. Upload to Azure Blob Storage (policies-source)
3. Process with PolicyChunker (Docling + PyMuPDF)
4. Generate embeddings and upload to Azure AI Search
5. Copy to policies-active for serving

Jobs are tracked in-memory with automatic cleanup after 24 hours.
"""

import os
import uuid
import asyncio
import logging
import tempfile
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, List
from threading import Lock

from azure.storage.blob import BlobServiceClient
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Processing job status."""
    QUEUED = "queued"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UploadJob:
    """Represents an upload processing job."""
    job_id: str
    filename: str
    status: JobStatus
    progress: int = 0  # 0-100
    chunks_created: int = 0
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status.value,
            "progress": self.progress,
            "chunks_created": self.chunks_created,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def update(self, status: JobStatus, progress: int = None, error: str = None, chunks: int = None):
        """Update job status."""
        self.status = status
        if progress is not None:
            self.progress = progress
        if error is not None:
            self.error = error
        if chunks is not None:
            self.chunks_created = chunks
        self.updated_at = datetime.now(timezone.utc)


class UploadService:
    """
    Service for handling PDF uploads and processing.

    Uses in-memory job tracking with automatic cleanup.
    For production scale, consider using Redis or a database.
    """

    # Configuration
    MAX_FILE_SIZE_MB = 50
    ALLOWED_EXTENSIONS = {".pdf"}
    JOB_TTL_HOURS = 24

    # PDF magic bytes
    PDF_MAGIC = b"%PDF"

    def __init__(self):
        self._jobs: Dict[str, UploadJob] = {}
        self._lock = Lock()

        # Azure Blob Storage configuration
        self.storage_connection_string = os.environ.get("STORAGE_CONNECTION_STRING")
        self.source_container = os.environ.get("SOURCE_CONTAINER_NAME", "policies-source")
        self.active_container = os.environ.get("CONTAINER_NAME", "policies-active")

        if self.storage_connection_string:
            self.blob_service = BlobServiceClient.from_connection_string(
                self.storage_connection_string
            )
            logger.info(f"UploadService initialized with storage containers: {self.source_container}, {self.active_container}")
        else:
            self.blob_service = None
            logger.warning("UploadService: STORAGE_CONNECTION_STRING not set - uploads will fail")

    @property
    def is_configured(self) -> bool:
        """Check if service is properly configured."""
        return self.blob_service is not None

    def validate_file(self, filename: str, file_content: bytes) -> tuple[bool, str]:
        """
        Validate uploaded file.

        Returns:
            (is_valid, error_message)
        """
        # Check extension
        ext = Path(filename).suffix.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return False, f"Invalid file type. Only PDF files are supported."

        # Check size
        size_mb = len(file_content) / (1024 * 1024)
        if size_mb > self.MAX_FILE_SIZE_MB:
            return False, f"File too large. Maximum size is {self.MAX_FILE_SIZE_MB}MB."

        # Check PDF magic bytes
        if not file_content.startswith(self.PDF_MAGIC):
            return False, "Invalid PDF file. File does not appear to be a valid PDF."

        return True, ""

    def create_job(self, filename: str) -> UploadJob:
        """Create a new upload job."""
        job_id = str(uuid.uuid4())[:8]
        job = UploadJob(
            job_id=job_id,
            filename=filename,
            status=JobStatus.QUEUED
        )

        with self._lock:
            self._jobs[job_id] = job
            self._cleanup_old_jobs()

        return job

    def get_job(self, job_id: str) -> Optional[UploadJob]:
        """Get job by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> List[UploadJob]:
        """List recent jobs, newest first."""
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.created_at,
                reverse=True
            )
            return jobs[:limit]

    def _cleanup_old_jobs(self):
        """Remove jobs older than TTL."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.JOB_TTL_HOURS)
        expired = [
            job_id for job_id, job in self._jobs.items()
            if job.created_at < cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired upload jobs")

    async def upload_to_blob(self, filename: str, content: bytes, container: str) -> str:
        """
        Upload file to Azure Blob Storage asynchronously.

        Uses azure.storage.blob.aio for true async I/O to avoid blocking the event loop.

        Returns:
            Blob URL
        """
        if not self.is_configured:
            raise RuntimeError("Storage not configured")

        # Use async client for non-blocking upload
        async with AsyncBlobServiceClient.from_connection_string(
            self.storage_connection_string
        ) as async_blob_service:
            container_client = async_blob_service.get_container_client(container)
            blob_client = container_client.get_blob_client(filename)

            # Async upload with overwrite
            await blob_client.upload_blob(content, overwrite=True)

            # Return the URL (construct from sync client since async doesn't expose .url directly)
            return f"https://{async_blob_service.account_name}.blob.core.windows.net/{container}/{filename}"

    async def process_pdf(self, job: UploadJob, file_content: bytes):
        """
        Process PDF in background: chunk, embed, and index.

        This is the main processing pipeline that runs after file upload.
        """
        temp_path = None

        try:
            # Phase 1: Upload to blob storage (10%)
            job.update(JobStatus.UPLOADING, progress=5)

            await self.upload_to_blob(
                job.filename,
                file_content,
                self.source_container
            )
            job.update(JobStatus.UPLOADING, progress=10)
            logger.info(f"[{job.job_id}] Uploaded {job.filename} to {self.source_container}")

            # Phase 2: Process with PolicyChunker (10-60%)
            job.update(JobStatus.PROCESSING, progress=15)

            # Save to temp file for processing
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_content)
                temp_path = tmp.name

            # Import chunker (heavy import, do it here)
            from preprocessing.chunker import PolicyChunker, ProcessingStatus

            chunker = PolicyChunker(max_chunk_size=1500)
            job.update(JobStatus.PROCESSING, progress=20)

            result = chunker.process_pdf_with_status(temp_path)
            job.update(JobStatus.PROCESSING, progress=50)

            if result.is_error:
                raise ValueError(f"PDF processing failed: {result.error_message}")

            if result.is_empty or not result.chunks:
                raise ValueError("PDF contained no extractable content")

            chunks = result.chunks
            job.update(JobStatus.PROCESSING, progress=60, chunks=len(chunks))
            logger.info(f"[{job.job_id}] Created {len(chunks)} chunks from {job.filename}")

            # Phase 3: Upload to Azure AI Search (60-90%)
            job.update(JobStatus.INDEXING, progress=65)

            # Import search index
            from azure_policy_index import PolicySearchIndex

            search_index = PolicySearchIndex()

            # Convert chunks to Azure documents
            documents = [chunk.to_azure_document() for chunk in chunks]
            job.update(JobStatus.INDEXING, progress=70)

            # Upload in batches
            upload_result = search_index.upload_chunks(chunks)
            job.update(JobStatus.INDEXING, progress=85)

            if upload_result.get("failed", 0) > 0:
                logger.warning(
                    f"[{job.job_id}] Some chunks failed to upload: "
                    f"{upload_result['failed']}/{upload_result['uploaded']}"
                )

            # Phase 4: Copy to active container (90-100%)
            job.update(JobStatus.INDEXING, progress=90)

            await self.upload_to_blob(
                job.filename,
                file_content,
                self.active_container
            )

            job.update(JobStatus.COMPLETED, progress=100, chunks=len(chunks))
            logger.info(
                f"[{job.job_id}] Completed processing {job.filename}: "
                f"{len(chunks)} chunks indexed"
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[{job.job_id}] Processing failed: {error_msg}")
            job.update(JobStatus.FAILED, error=error_msg)

        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    async def start_upload(self, filename: str, file_content: bytes) -> UploadJob:
        """
        Start the upload and processing workflow.

        Returns immediately with job info, processing continues in background.
        """
        # Validate
        is_valid, error = self.validate_file(filename, file_content)
        if not is_valid:
            raise ValueError(error)

        if not self.is_configured:
            raise RuntimeError("Upload service not configured. Check STORAGE_CONNECTION_STRING.")

        # Create job
        job = self.create_job(filename)
        logger.info(f"[{job.job_id}] Created upload job for {filename}")

        # Start background processing
        asyncio.create_task(self.process_pdf(job, file_content))

        return job


# Singleton instance
_upload_service: Optional[UploadService] = None


def get_upload_service() -> UploadService:
    """Get or create the upload service singleton."""
    global _upload_service
    if _upload_service is None:
        _upload_service = UploadService()
    return _upload_service
