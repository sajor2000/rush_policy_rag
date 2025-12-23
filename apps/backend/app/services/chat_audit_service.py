"""
Chat Audit Service - Non-blocking audit logging for RAG quality monitoring.

Design:
- Non-blocking: Uses asyncio.create_task() for fire-and-forget logging
- Buffered: Batches records to reduce blob API calls
- Resilient: Failures don't affect chat responses
- Date-partitioned: YYYY/MM/DD.jsonl structure

Usage:
    # On startup (in lifespan):
    await init_chat_audit_service()

    # In chat endpoint (fire-and-forget):
    asyncio.create_task(
        get_chat_audit_service().log_chat(request, response, latency_ms)
    )

    # On shutdown:
    await shutdown_chat_audit_service()
"""

import asyncio
import json
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContainerClient

from app.core.config import settings
from app.models.audit_schemas import AuditCitation, ChatAuditRecord
from app.models.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


class ChatAuditService:
    """
    Non-blocking audit service for chat interactions.

    Thread-safe buffer with background flush to Azure Blob Storage.
    Records stored as JSONL files partitioned by date.
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        container_name: Optional[str] = None,
        buffer_size: Optional[int] = None,
        flush_interval: Optional[int] = None,
    ):
        self._connection_string = connection_string or settings.STORAGE_CONNECTION_STRING
        self._container_name = container_name or settings.CHAT_AUDIT_CONTAINER
        self._buffer_size = buffer_size or settings.CHAT_AUDIT_BUFFER_SIZE
        self._flush_interval = flush_interval or settings.CHAT_AUDIT_FLUSH_INTERVAL_SECONDS

        # Thread-safe buffer
        self._buffer: deque[ChatAuditRecord] = deque(maxlen=self._buffer_size * 2)
        self._buffer_lock = asyncio.Lock()

        # Background task management
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

        # Lazy-initialized blob client
        self._blob_service: Optional[BlobServiceClient] = None
        self._container_client: Optional[ContainerClient] = None

        logger.info(
            f"ChatAuditService initialized: container={self._container_name}, "
            f"buffer_size={self._buffer_size}, flush_interval={self._flush_interval}s"
        )

    @property
    def is_enabled(self) -> bool:
        """Check if audit logging is enabled."""
        return settings.CHAT_AUDIT_ENABLED and bool(self._connection_string)

    async def start(self) -> None:
        """Start the background flush task."""
        if not self.is_enabled:
            logger.warning("Chat audit disabled - skipping start (no connection string or disabled)")
            return

        if self._running:
            return

        # Initialize blob client
        await self._ensure_container()

        # Start background flush task
        self._running = True
        self._flush_task = asyncio.create_task(self._background_flush_loop())
        logger.info("ChatAuditService started - background flush task running")

    async def stop(self) -> None:
        """Stop the background flush task and flush remaining records."""
        if not self._running:
            return

        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self._flush_buffer()
        logger.info("ChatAuditService stopped - all records flushed")

    async def log_chat(
        self,
        request: ChatRequest,
        response: ChatResponse,
        latency_ms: int,
        pipeline_used: str = "cohere_rerank",
        search_query: Optional[str] = None,
    ) -> None:
        """
        Log a chat interaction (non-blocking).

        Called via asyncio.create_task() for fire-and-forget behavior.
        Failures are logged but never raise to caller.
        """
        if not self.is_enabled:
            return

        try:
            # Build audit record
            record = self._build_audit_record(
                request=request,
                response=response,
                latency_ms=latency_ms,
                pipeline_used=pipeline_used,
                search_query=search_query,
            )

            # Add to buffer (thread-safe)
            async with self._buffer_lock:
                self._buffer.append(record)
                buffer_len = len(self._buffer)

            # Trigger immediate flush if buffer is full
            if buffer_len >= self._buffer_size:
                asyncio.create_task(self._flush_buffer())

        except Exception as e:
            # CRITICAL: Never let audit failures affect chat responses
            logger.error(f"Chat audit logging failed (non-critical): {e}")

    def _build_audit_record(
        self,
        request: ChatRequest,
        response: ChatResponse,
        latency_ms: int,
        pipeline_used: str,
        search_query: Optional[str],
    ) -> ChatAuditRecord:
        """Build a ChatAuditRecord from request/response."""
        # Truncate long fields to prevent storage bloat
        question = request.message[:settings.CHAT_AUDIT_MAX_QUESTION_LENGTH]
        response_text = response.response[:settings.CHAT_AUDIT_MAX_RESPONSE_LENGTH]
        summary_text = response.summary[:500] if response.summary else ""

        # Extract citations from evidence
        citations = [
            AuditCitation(
                title=e.title,
                reference_number=e.reference_number,
                section=e.section,
                source_file=e.source_file,
                reranker_score=e.reranker_score,
            )
            for e in (response.evidence or [])[:10]  # Limit to top 10
        ]

        return ChatAuditRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            question=question,
            filter_applies_to=request.filter_applies_to,
            response=response_text,
            summary=summary_text,
            found=response.found,
            citations=citations,
            chunks_used=response.chunks_used,
            confidence=response.confidence,
            confidence_score=response.confidence_score,
            needs_human_review=response.needs_human_review,
            safety_flags=response.safety_flags or [],
            latency_ms=latency_ms,
            pipeline_used=pipeline_used,
            search_query=search_query,
        )

    async def _background_flush_loop(self) -> None:
        """Background task that periodically flushes the buffer."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background flush failed: {e}")

    async def _flush_buffer(self) -> None:
        """Flush buffered records to Azure Blob Storage."""
        # Get records from buffer (thread-safe)
        async with self._buffer_lock:
            if not self._buffer:
                return
            records = list(self._buffer)
            self._buffer.clear()

        if not records:
            return

        # Group by date
        records_by_date: dict[str, List[ChatAuditRecord]] = {}
        for record in records:
            date_key = record.timestamp.strftime("%Y/%m/%d")
            if date_key not in records_by_date:
                records_by_date[date_key] = []
            records_by_date[date_key].append(record)

        # Write to each date's file
        for date_path, date_records in records_by_date.items():
            try:
                await self._append_to_blob(date_path, date_records)
                logger.debug(f"Flushed {len(date_records)} audit records to {date_path}.jsonl")
            except Exception as e:
                logger.error(f"Failed to flush audit records to {date_path}: {e}")
                # Re-add failed records to buffer for retry
                async with self._buffer_lock:
                    for record in date_records:
                        self._buffer.appendleft(record)

    async def _append_to_blob(self, date_path: str, records: List[ChatAuditRecord]) -> None:
        """
        Append records to a date-partitioned JSONL blob.

        Strategy: Download existing content, append new records, upload.
        This is necessary because BlockBlob doesn't support append operations.
        """
        blob_name = f"{date_path}.jsonl"
        blob_client = self._container_client.get_blob_client(blob_name)

        # Download existing content (if any)
        existing_content = ""
        try:
            download = await asyncio.to_thread(blob_client.download_blob)
            existing_bytes = await asyncio.to_thread(download.readall)
            existing_content = existing_bytes.decode("utf-8")
        except ResourceNotFoundError:
            existing_content = ""
        except Exception as e:
            logger.warning(f"Failed to download existing blob {blob_name}: {e}")
            existing_content = ""

        # Append new records as JSONL
        new_lines = [record.model_dump_json() for record in records]
        new_content = "\n".join(new_lines)

        if existing_content:
            # Ensure existing content ends with newline
            if not existing_content.endswith("\n"):
                existing_content += "\n"
            full_content = existing_content + new_content + "\n"
        else:
            full_content = new_content + "\n"

        # Upload merged content
        await asyncio.to_thread(
            blob_client.upload_blob,
            full_content.encode("utf-8"),
            overwrite=True,
        )

    async def _ensure_container(self) -> None:
        """Ensure the audit container exists."""
        if self._blob_service is None:
            self._blob_service = await asyncio.to_thread(
                BlobServiceClient.from_connection_string,
                self._connection_string,
            )

        self._container_client = self._blob_service.get_container_client(self._container_name)

        try:
            exists = await asyncio.to_thread(self._container_client.exists)
            if not exists:
                await asyncio.to_thread(self._container_client.create_container)
                logger.info(f"Created audit container: {self._container_name}")
        except Exception as e:
            logger.error(f"Failed to ensure audit container: {e}")
            raise

    # =========================================================================
    # Query Methods (for Admin API)
    # =========================================================================

    async def get_records_for_date(
        self,
        date: str,
        limit: int = 100,
        offset: int = 0,
        found: Optional[bool] = None,
        confidence: Optional[str] = None,
        needs_human_review: Optional[bool] = None,
    ) -> tuple[List[ChatAuditRecord], int]:
        """
        Query audit records for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format
            limit: Max records to return
            offset: Pagination offset
            found: Filter by found status
            confidence: Filter by confidence level
            needs_human_review: Filter by needs_human_review flag

        Returns:
            Tuple of (records, total_count)
        """
        if not self._container_client:
            await self._ensure_container()

        blob_name = f"{date.replace('-', '/')}.jsonl"
        blob_client = self._container_client.get_blob_client(blob_name)

        try:
            download = await asyncio.to_thread(blob_client.download_blob)
            content_bytes = await asyncio.to_thread(download.readall)
            content = content_bytes.decode("utf-8")
        except ResourceNotFoundError:
            return [], 0

        # Parse JSONL
        records = []
        for line in content.strip().split("\n"):
            if line:
                try:
                    record = ChatAuditRecord.model_validate_json(line)

                    # Apply filters
                    if found is not None and record.found != found:
                        continue
                    if confidence is not None and record.confidence != confidence:
                        continue
                    if needs_human_review is not None and record.needs_human_review != needs_human_review:
                        continue

                    records.append(record)
                except Exception as e:
                    logger.warning(f"Failed to parse audit record: {e}")

        total_count = len(records)

        # Apply pagination
        records = records[offset : offset + limit]

        return records, total_count

    async def get_stats_for_date(self, date: str) -> dict:
        """Get aggregated statistics for a specific date."""
        records, total = await self.get_records_for_date(date, limit=10000)

        if not records:
            return {"date": date, "total_queries": 0}

        # Calculate stats
        found_count = sum(1 for r in records if r.found)
        confidence_breakdown = {"high": 0, "medium": 0, "low": 0}
        for r in records:
            confidence_breakdown[r.confidence] += 1

        latencies = [r.latency_ms for r in records]
        latencies.sort()

        all_flags = []
        for r in records:
            all_flags.extend(r.safety_flags)

        pipeline_breakdown: dict[str, int] = {}
        for r in records:
            pipeline_breakdown[r.pipeline_used] = pipeline_breakdown.get(r.pipeline_used, 0) + 1

        return {
            "date": date,
            "total_queries": total,
            "found_count": found_count,
            "not_found_count": total - found_count,
            "confidence_breakdown": confidence_breakdown,
            "needs_review_count": sum(1 for r in records if r.needs_human_review),
            "safety_flags_count": sum(1 for r in records if r.safety_flags),
            "unique_safety_flags": list(set(all_flags)),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_latency_ms": latencies[int(len(latencies) * 0.95)] if latencies else 0,
            "pipeline_breakdown": pipeline_breakdown,
        }

    async def list_available_dates(self, limit: int = 30) -> List[str]:
        """List dates that have audit records (most recent first)."""
        if not self._container_client:
            await self._ensure_container()

        dates = []

        try:
            blobs = await asyncio.to_thread(list, self._container_client.list_blobs())
            for blob in blobs:
                if blob.name.endswith(".jsonl"):
                    # Convert "2024/01/15.jsonl" to "2024-01-15"
                    date_str = blob.name.replace("/", "-").replace(".jsonl", "")
                    dates.append(date_str)
        except Exception as e:
            logger.error(f"Failed to list audit dates: {e}")

        # Sort by date descending
        dates.sort(reverse=True)
        return dates[:limit]


# ============================================================================
# Singleton Instance
# ============================================================================

_audit_service: Optional[ChatAuditService] = None


def get_chat_audit_service() -> ChatAuditService:
    """Get the singleton ChatAuditService instance."""
    global _audit_service
    if _audit_service is None:
        _audit_service = ChatAuditService()
    return _audit_service


async def init_chat_audit_service() -> ChatAuditService:
    """Initialize and start the ChatAuditService."""
    service = get_chat_audit_service()
    await service.start()
    return service


async def shutdown_chat_audit_service() -> None:
    """Stop the ChatAuditService and flush remaining records."""
    global _audit_service
    if _audit_service is not None:
        await _audit_service.stop()
        _audit_service = None
