"""
Policy Sync Manager - Differential Sync for Monthly Policy Updates

This module handles the monthly sync pipeline:
- Detects new, changed, and deleted documents via content hashing
- Only processes documents that have actually changed
- Updates Azure Search index with new chunks
- Maintains audit trail via blob metadata

For 1800 documents where only 10-50 change monthly, this ensures:
- Processing time: ~3-5 minutes (vs ~3 hours for full reindex)
- Cost optimization: Only embed changed documents
- Clean updates: Old chunks deleted before new ones added

Usage:
    from policy_sync import PolicySyncManager

    sync = PolicySyncManager()

    # Monthly sync from staging to production
    report = sync.sync_monthly(
        source_container="policy-monthly",
        target_container="policies-active"
    )

    # Or process a single document
    sync.process_single_document("new-policy.pdf")
"""

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobClient, BlobServiceClient

# Configure logging
logger = logging.getLogger(__name__)

# Configuration constants
MAX_METADATA_SIZE = 7500  # Azure Blob metadata limit (8KB with buffer)
DEFAULT_BATCH_SIZE = 100  # Default batch size for operations
MAX_SEARCH_RESULTS = 1000  # Azure AI Search page size
MAX_DELETE_BATCHES = 200  # Safety limit for pagination (200K chunks max)
TEMP_FILE_SUFFIX = ".pdf"  # Temporary file extension
SYNC_BATCH_ID_PATTERN = r"^\d{4}-\d{2}$"  # YYYY-MM format for sync batch IDs

from app.core.security import escape_odata_string
from azure_policy_index import PolicySearchIndex
from preprocessing.chunker import PolicyChunk, PolicyChunker

# Configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
SOURCE_CONTAINER = os.environ.get("SOURCE_CONTAINER", "policy-monthly")
TARGET_CONTAINER = os.environ.get("CONTAINER_NAME", "policies-active")


def utc_now_iso() -> str:
    """Return UTC ISO-8601 timestamp with explicit timezone for DateTimeOffset fields."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class DocumentState:
    """Tracks the state of a document for sync purposes with version control."""

    filename: str
    content_hash: str
    chunk_ids: List[str] = field(default_factory=list)
    processed_date: str = ""
    reference_number: str = ""
    policy_number: str = ""
    title: str = ""
    # Version control fields for monthly updates (v1 → v2 transitions)
    version_number: str = "1.0"
    version_sequence: int = 1
    effective_date: str = ""
    policy_status: str = "ACTIVE"  # ACTIVE, SUPERSEDED, RETIRED, DRAFT

    def to_metadata(self) -> Dict[str, str]:
        """Convert to blob metadata format (all values must be strings)."""
        chunk_ids_json = json.dumps(self.chunk_ids)

        # Validate size (8KB limit for Azure Blob metadata)
        if len(chunk_ids_json) > MAX_METADATA_SIZE:
            # Store count only, rely on search index for IDs
            chunk_ids_json = json.dumps(
                {"count": len(self.chunk_ids), "truncated": True}
            )
            logger.warning(
                f"Metadata truncated for {self.filename}: {len(self.chunk_ids)} chunks"
            )

        return {
            "content_hash": self.content_hash,
            "chunk_ids": chunk_ids_json,
            "processed_date": self.processed_date,
            "reference_number": self.reference_number,
            "policy_number": self.policy_number,
            "title": self.title,
            # Version control metadata
            "version_number": self.version_number,
            "version_sequence": str(self.version_sequence),
            "effective_date": self.effective_date,
            "policy_status": self.policy_status,
        }

    @classmethod
    def from_metadata(cls, filename: str, metadata: Dict[str, str]) -> "DocumentState":
        """Create from blob metadata."""
        chunk_ids = []
        if "chunk_ids" in metadata:
            try:
                parsed = json.loads(metadata["chunk_ids"])
                # Handle truncated metadata case
                if isinstance(parsed, dict) and parsed.get("truncated"):
                    chunk_ids = []  # Will need to query index for actual IDs
                    logger.debug(f"Truncated chunk_ids for {filename}")
                elif isinstance(parsed, list):
                    chunk_ids = parsed
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse chunk_ids for {filename}: {e}")

        # Parse version_sequence safely
        try:
            version_sequence = int(metadata.get("version_sequence", "1"))
        except (ValueError, TypeError):
            version_sequence = 1

        return cls(
            filename=filename,
            content_hash=metadata.get("content_hash", ""),
            chunk_ids=chunk_ids,
            processed_date=metadata.get("processed_date", ""),
            reference_number=metadata.get("reference_number", ""),
            policy_number=metadata.get("policy_number", ""),
            title=metadata.get("title", ""),
            # Version control fields
            version_number=metadata.get("version_number", "1.0"),
            version_sequence=version_sequence,
            effective_date=metadata.get("effective_date", ""),
            policy_status=metadata.get("policy_status", "ACTIVE"),
        )

    def increment_version(self) -> "DocumentState":
        """Create a new DocumentState with incremented version for v1 → v2 transitions."""
        new_sequence = self.version_sequence + 1
        return DocumentState(
            filename=self.filename,
            content_hash=self.content_hash,
            chunk_ids=[],  # Will be populated after processing
            processed_date=datetime.now().isoformat(),
            reference_number=self.reference_number,
            policy_number=self.policy_number,
            title=self.title,
            version_number=f"{new_sequence}.0",
            version_sequence=new_sequence,
            effective_date=datetime.now().isoformat(),
            policy_status="ACTIVE",
        )


@dataclass
class SyncReport:
    """Report of sync operation results with version tracking."""

    started_at: str
    completed_at: str = ""
    source_container: str = ""
    target_container: str = ""
    documents_scanned: int = 0
    documents_new: int = 0
    documents_changed: int = 0
    documents_unchanged: int = 0
    documents_deleted: int = 0
    documents_quarantined: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    chunks_superseded: int = 0  # Chunks marked as SUPERSEDED (not deleted)
    version_transitions: List[Dict] = field(default_factory=list)  # v1→v2 tracking
    errors: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def add_version_transition(self, filename: str, old_version: str, new_version: str):
        """Track a version transition for the audit log."""
        self.version_transitions.append(
            {
                "filename": filename,
                "old_version": old_version,
                "new_version": new_version,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def log_summary(self):
        """Log human-readable summary."""
        summary = f"""
{'=' * 60}
SYNC REPORT (with Version Tracking)
{'=' * 60}
Started: {self.started_at}
Completed: {self.completed_at}
Source: {self.source_container}
Target: {self.target_container}
{'-' * 60}
Documents scanned: {self.documents_scanned}
  New: {self.documents_new}
  Changed (version upgraded): {self.documents_changed}
  Unchanged: {self.documents_unchanged}
  Deleted/Retired: {self.documents_deleted}
  Quarantined: {self.documents_quarantined}
{'-' * 60}
Chunks created: {self.chunks_created}
Chunks superseded: {self.chunks_superseded}
Chunks deleted: {self.chunks_deleted}"""

        if self.version_transitions:
            summary += (
                f"\n{'-' * 60}\nVersion Transitions ({len(self.version_transitions)}):"
            )
            for vt in self.version_transitions[:10]:
                summary += (
                    f"\n  {vt['filename']}: v{vt['old_version']} → v{vt['new_version']}"
                )
            if len(self.version_transitions) > 10:
                summary += f"\n  ... and {len(self.version_transitions) - 10} more"

        if self.errors:
            summary += f"\n{'-' * 60}\nErrors: {len(self.errors)}"
            for err in self.errors[:5]:
                summary += f"\n  - {err['file']}: {err['error']}"

        logger.info(summary)


class MetadataValidationError(RuntimeError):
    """Raised when a document fails required metadata contract validation."""

    def __init__(self, filename: str, issues: List[str], details: Dict[str, Any]):
        self.filename = filename
        self.issues = issues
        self.details = details
        super().__init__(
            f"Metadata validation failed for {filename}: {', '.join(issues)}"
        )


class PolicySyncManager:
    """
    Manages differential sync of policy documents.

    Workflow:
    1. Scan source container for new/changed documents
    2. Compare content hashes with target container
    3. For changed documents:
       a. Delete old chunks from search index
       b. Process PDF into new chunks
       c. Upload new chunks with embeddings
       d. Update blob metadata
    4. Copy synced files to target container
    5. Generate audit report

    Uses IBM Docling for PDF parsing with:
    - TableFormer model for accurate table extraction
    - Native checkbox detection for "Applies To" fields
    - Hierarchical section-aware chunking

    Note: Legacy PyMuPDF backend is archived in preprocessing/archive/
    """

    def __init__(
        self,
        connection_string: str = STORAGE_CONNECTION_STRING,
        chunker: Optional[PolicyChunker] = None,
        search_index: Optional[PolicySearchIndex] = None,
        use_docling: Optional[
            bool
        ] = None,  # Deprecated, kept for backward compatibility
        backend: Optional[str] = None,  # Deprecated, kept for backward compatibility
    ):
        self.blob_service = BlobServiceClient.from_connection_string(connection_string)

        # Initialize chunker (always uses Docling now)
        self.chunker = chunker or PolicyChunker(max_chunk_size=1500)

        self.search_index = search_index or PolicySearchIndex()
        logger.info("PolicySyncManager initialized with Docling backend")

    def compute_content_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of document content."""
        return hashlib.sha256(content).hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((HttpResponseError, ConnectionError)),
    )
    def compute_content_hash_streaming(self, blob_client: BlobClient) -> str:
        """
        Compute SHA-256 hash using streaming with retry logic.

        Retries on network errors to prevent full sync failure from transient issues.
        For 1800 docs × 5MB = 9GB if loaded all at once.
        Streaming processes one chunk at a time.

        Args:
            blob_client: Azure Blob client for the file

        Returns:
            SHA-256 hex digest

        Raises:
            HttpResponseError: After 3 retry attempts
            ConnectionError: After 3 retry attempts
        """
        hash_obj = hashlib.sha256()
        try:
            download_stream = blob_client.download_blob()
            for chunk in download_stream.chunks():
                hash_obj.update(chunk)
            return hash_obj.hexdigest()
        except Exception as e:
            logger.warning(f"Hash computation failed for {blob_client.blob_name}: {e}")
            raise

    def get_document_state(
        self, container: str, filename: str
    ) -> Optional[DocumentState]:
        """Get the current state of a document from blob metadata."""
        try:
            container_client = self.blob_service.get_container_client(container)
            blob_client = container_client.get_blob_client(filename)
            properties = blob_client.get_blob_properties()
            metadata = properties.metadata or {}

            return DocumentState.from_metadata(filename, metadata)
        except ResourceNotFoundError:
            return None

    def detect_changes(
        self, source_container: str, target_container: str
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Detect new, changed, and deleted documents.

        Returns:
            Tuple of (new_files, changed_files, deleted_files)
        """
        source_client = self.blob_service.get_container_client(source_container)
        target_client = self.blob_service.get_container_client(target_container)

        # Get all files in source using streaming hash to avoid memory explosion
        source_files = {}
        for blob in source_client.list_blobs():
            if blob.name.endswith(".pdf"):
                # Use streaming hash to avoid loading entire PDF into memory
                blob_client = source_client.get_blob_client(blob.name)
                source_files[blob.name] = self.compute_content_hash_streaming(
                    blob_client
                )

        # Get all files in target with their hashes
        target_files = {}
        for blob in target_client.list_blobs(include=["metadata"]):
            if blob.name.endswith(".pdf"):
                metadata = blob.metadata or {}
                target_files[blob.name] = metadata.get("content_hash", "")

        # Categorize
        new_files = []
        changed_files = []
        deleted_files = []

        # Check source files
        for filename, source_hash in source_files.items():
            if filename not in target_files:
                new_files.append(filename)
            elif source_hash != target_files[filename]:
                changed_files.append(filename)
            # else: unchanged

        # Check for deleted files (in target but not in source)
        for filename in target_files:
            if filename not in source_files:
                deleted_files.append(filename)

        return new_files, changed_files, deleted_files

    def validate_hash_consistency(self, container: str, sample_size: int = 10) -> dict:
        """
        Validate that stored hashes match recomputed hashes.

        This helps detect issues like:
        - Hash algorithm changes
        - Corrupted blob metadata
        - Encoding issues

        Args:
            container: Container name to validate
            sample_size: Number of documents to check (default 10)

        Returns:
            Dict with validation results:
            - total_checked: Number of documents validated
            - matches: Number of hash matches
            - mismatches: List of mismatched documents
            - errors: List of errors encountered
        """
        container_client = self.blob_service.get_container_client(container)
        results = {"total_checked": 0, "matches": 0, "mismatches": [], "errors": []}

        try:
            blobs = list(container_client.list_blobs(include=["metadata"]))
        except Exception as e:
            results["errors"].append(f"Failed to list blobs: {e}")
            return results

        # Filter to PDFs and sample
        pdf_blobs = [b for b in blobs if b.name.lower().endswith(".pdf")]
        sample = pdf_blobs[:sample_size]

        for blob in sample:
            results["total_checked"] += 1

            # Get stored hash from metadata
            stored_hash = ""
            if blob.metadata:
                stored_hash = blob.metadata.get("content_hash", "")

            try:
                # Recompute hash
                blob_client = container_client.get_blob_client(blob.name)
                computed_hash = self.compute_content_hash_streaming(blob_client)

                if stored_hash == computed_hash:
                    results["matches"] += 1
                elif not stored_hash:
                    results["mismatches"].append(
                        {
                            "filename": blob.name,
                            "issue": "No stored hash in metadata",
                            "computed": computed_hash[:16] + "...",
                        }
                    )
                else:
                    results["mismatches"].append(
                        {
                            "filename": blob.name,
                            "stored": stored_hash[:16] + "...",
                            "computed": computed_hash[:16] + "...",
                            "issue": "Hash mismatch",
                        }
                    )
            except Exception as e:
                results["errors"].append({"filename": blob.name, "error": str(e)})

        return results

    def _extract_policy_number_from_filename(self, filename: str) -> str:
        """Best-effort policy number extraction from filename."""
        pattern = re.compile(
            r"\b([A-Za-z]{2})\s*-\s*([A-Za-z])\s*(\d{1,2})(?:\.(\d{1,4}))?\b"
        )
        match = pattern.search(filename or "")
        if not match:
            return ""

        prefix = match.group(1).upper()
        letter = match.group(2).upper()
        major = match.group(3)
        minor = match.group(4)

        if minor is None:
            return f"{prefix}-{letter} {major.zfill(2)}.00"
        if len(minor) == 3 and major == "0":
            digits = f"{major}{minor}".zfill(4)[:4]
            return f"{prefix}-{letter} {digits[:2]}.{digits[2:]}"
        minor2 = (minor + "0")[:2] if len(minor) == 1 else minor[:2]
        return f"{prefix}-{letter} {major.zfill(2)}.{minor2}"

    def _extract_document_id_from_filename(self, filename: str) -> str:
        """Extract parenthesized numeric document ID from filename as fallback reference_number.

        Examples:
            'Employee Appeals Policy (2523).pdf' → '2523'
            'Some Policy (5323).pdf' → '5323'
            'HR-C 05.00 Some Policy.pdf' → ''  (has XX-X format, skip)
        """
        match = re.search(r"\((\d{2,6})\)", filename or "")
        return match.group(1) if match else ""

    def _autofill_chunk_metadata(
        self, chunks: List[PolicyChunk], target_filename: str
    ) -> None:
        """Apply conservative metadata fallback when gate mode is autofill or quarantine."""
        inferred_policy_number = self._extract_policy_number_from_filename(
            target_filename
        )
        inferred_reference_number = self._extract_document_id_from_filename(
            target_filename
        )
        inferred_title = target_filename.replace(".pdf", "").strip()

        for chunk in chunks:
            if not chunk.source_file:
                chunk.source_file = target_filename
            if not chunk.policy_title:
                chunk.policy_title = inferred_title
            if not chunk.policy_number and inferred_policy_number:
                chunk.policy_number = inferred_policy_number
            if (
                not chunk.reference_number
                and not chunk.policy_number
                and inferred_reference_number
            ):
                chunk.reference_number = inferred_reference_number
            if chunk.page_number is None:
                chunk.page_number = max(1, (int(chunk.chunk_index) // 2) + 1)

    def _validate_document_metadata_contract(
        self,
        *,
        filename: str,
        chunks: List[PolicyChunk],
        content_hash: str,
        processed_date: str,
    ) -> Dict[str, Any]:
        """
        Validate per-PDF and per-chunk metadata contract.

        Returns:
            Dict with valid flag and detailed issue list for reporting.
        """
        issues: List[str] = []
        per_chunk_issues: List[Dict[str, Any]] = []
        page_number_chunks = 0

        if not chunks:
            issues.append("chunk_ids empty")

        for idx, chunk in enumerate(chunks):
            chunk_missing: List[str] = []
            if not chunk.source_file:
                chunk_missing.append("source_file")
            # PolicyChunk stores raw text in `text` (not `content`).
            chunk_text = getattr(chunk, "text", getattr(chunk, "content", ""))
            if not (chunk_text or "").strip():
                chunk_missing.append("content")
            if chunk.chunk_index is None:
                chunk_missing.append("chunk_index")
            if not (chunk.policy_title or "").strip():
                chunk_missing.append("policy_title")
            if not (
                (chunk.policy_number or "").strip()
                or (chunk.reference_number or "").strip()
            ):
                chunk_missing.append("policy_number_or_reference_number")
            if chunk.page_number is not None:
                page_number_chunks += 1

            if chunk_missing:
                per_chunk_issues.append(
                    {
                        "chunk_index": idx,
                        "missing_fields": chunk_missing,
                    }
                )

        if chunks and page_number_chunks == 0:
            issues.append("page_number missing on all chunks")

        first = chunks[0] if chunks else None
        title = (first.policy_title if first else "") or ""
        policy_number = (first.policy_number if first else "") or ""
        reference_number = (first.reference_number if first else "") or ""

        if not filename:
            issues.append("source_file")
        if not content_hash:
            issues.append("content_hash")
        if not processed_date:
            issues.append("processed_date")
        if not title.strip():
            issues.append("title")
        if not (policy_number.strip() or reference_number.strip()):
            issues.append("policy_number_or_reference_number")
        if not chunks:
            issues.append("chunk_ids")

        if per_chunk_issues:
            issues.append("chunk_metadata_missing_fields")

        report = {
            "filename": filename,
            "valid": len(issues) == 0,
            "issues": sorted(set(issues)),
            "per_chunk_issues": per_chunk_issues,
            "summary": {
                "chunk_count": len(chunks),
                "page_number_chunks": page_number_chunks,
                "page_number_rate": (
                    (page_number_chunks / len(chunks)) if chunks else 0.0
                ),
                "title": title,
                "policy_number": policy_number,
                "reference_number": reference_number,
            },
        }
        return report

    def _write_json_report(
        self, output_path: Optional[str], payload: Dict[str, Any]
    ) -> None:
        """Write a JSON artifact if output_path is provided."""
        if not output_path:
            return
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def process_document(
        self,
        source_container: str,
        target_container: str,
        filename: str,
        is_update: bool = False,
        archive_old_version: bool = True,
        source_blob_name: Optional[str] = None,
        target_filename: Optional[str] = None,
        metadata_gate_mode: str = "fail",
        metadata_report_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[str], int, Optional[str]]:
        """
        Process a single document with version control and transaction safety.

        For version transitions (v1 → v2):
        - Old chunks are marked as SUPERSEDED (not deleted) for audit trail
        - New chunks get incremented version number
        - Both versions remain searchable with policy_status filter
        - Rollback to ACTIVE if upload fails (prevents data loss)

        Args:
            source_container: Container with the source PDF
            target_container: Container to copy processed PDF
            filename: Canonical filename (legacy path uses same value for source + target)
            is_update: If True, this is a version transition (v1 → v2)
            archive_old_version: If True, mark old chunks as SUPERSEDED instead of deleting
            source_blob_name: Optional source blob path (supports prefix-based monthly folders)
            target_filename: Optional target filename in active container/index
            metadata_gate_mode: fail | quarantine | autofill
            metadata_report_rows: Optional list to append per-file metadata report rows

        Returns:
            Tuple of (chunk_ids, superseded_count, version_transition_info)

        Raises:
            RuntimeError: If chunk upload fails completely
            Exception: For other processing errors
        """
        superseded_count = 0
        version_info = None
        old_version = "1.0"
        new_version = "1.0"
        rollback_required = False
        target_name = target_filename or filename
        source_name = source_blob_name or filename

        # Get existing document state for version tracking
        old_state = self.get_document_state(target_container, target_name)

        if is_update and old_state:
            old_version = old_state.version_number
            new_sequence = old_state.version_sequence + 1
            new_version = f"{new_sequence}.0"
            version_info = f"{old_version}→{new_version}"

            # Check for version conflicts (concurrent updates)
            search_client = self.search_index.get_search_client()
            safe_filename = escape_odata_string(target_name)
            safe_version = escape_odata_string(new_version)
            existing_new_version = list(
                search_client.search(
                    search_text="*",
                    filter=f"source_file eq '{safe_filename}' and version_number eq '{safe_version}'",
                    select=["id"],
                    top=1,
                )
            )

            if existing_new_version:
                raise RuntimeError(
                    f"Version conflict: {filename} v{new_version} already exists. "
                    f"Concurrent update detected. Retry sync operation."
                )

            if archive_old_version:
                # Mark old chunks as SUPERSEDED instead of deleting
                try:
                    superseded_count = self.supersede_old_chunks(
                        source_file=target_name, superseded_by=new_version
                    )
                    rollback_required = (
                        True  # Track that we need rollback if upload fails
                    )
                    logger.info(
                        f"Superseded {superseded_count} chunks for {target_name} (v{old_version} → v{new_version})"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to supersede old chunks for {target_name}: {e}"
                    )
                    raise  # Don't proceed if we can't supersede
            else:
                # Legacy behavior: delete old chunks
                superseded_count = self.search_index.delete_by_source_file(target_name)
        else:
            new_version = "1.0"

        # Download PDF from source
        source_client = self.blob_service.get_container_client(source_container)
        blob_data = source_client.get_blob_client(source_name).download_blob().readall()
        content_hash = self.compute_content_hash(blob_data)

        # Save temporarily for processing
        tmp_path = None
        with tempfile.NamedTemporaryFile(suffix=TEMP_FILE_SUFFIX, delete=False) as tmp:
            tmp.write(blob_data)
            tmp_path = tmp.name

        try:
            # Chunk the document
            chunks = self.chunker.process_pdf(tmp_path)

            # Update source_file and version info for all chunks
            current_time = utc_now_iso()
            for chunk in chunks:
                chunk.source_file = target_name
                # Apply version control fields
                chunk.version_number = new_version
                chunk.version_sequence = int(new_version.split(".")[0])
                chunk.version_date = current_time
                chunk.effective_date = current_time
                chunk.policy_status = "ACTIVE"

            if metadata_gate_mode not in {"fail", "quarantine", "autofill"}:
                raise ValueError(
                    f"Invalid metadata_gate_mode='{metadata_gate_mode}'. "
                    "Expected fail|quarantine|autofill."
                )

            if metadata_gate_mode in ("autofill", "quarantine"):
                self._autofill_chunk_metadata(chunks, target_name)

            metadata_row = self._validate_document_metadata_contract(
                filename=target_name,
                chunks=chunks,
                content_hash=content_hash,
                processed_date=current_time,
            )
            if metadata_report_rows is not None:
                metadata_report_rows.append(metadata_row)

            if not metadata_row["valid"]:
                raise MetadataValidationError(
                    target_name,
                    metadata_row["issues"],
                    metadata_row,
                )

            # Upload chunks to search index with error checking
            if chunks:
                upload_result = self.search_index.upload_chunks(chunks)

                # Check for upload failures
                if upload_result.get("failed", 0) > 0:
                    failed_count = upload_result["failed"]
                    uploaded_count = upload_result.get("uploaded", 0)

                    if uploaded_count == 0:
                        # Complete failure - rollback superseded chunks
                        if rollback_required:
                            logger.error(
                                f"Chunk upload failed completely for {target_name}. Rolling back..."
                            )
                            self._rollback_superseded_chunks(target_name, old_version)
                        raise RuntimeError(
                            f"Failed to upload any chunks for {target_name}: {failed_count} failed"
                        )
                    else:
                        # Partial failure - log warning but don't rollback
                        logger.warning(
                            f"Partial upload failure for {filename}: "
                            f"{uploaded_count} succeeded, {failed_count} failed"
                        )

            chunk_ids = [c.chunk_id for c in chunks]

            # Get metadata from first chunk (if available)
            ref_num = chunks[0].reference_number if chunks else ""
            policy_num = chunks[0].policy_number if chunks else ""
            title = chunks[0].policy_title if chunks else ""

            # Create document state with version info
            state = DocumentState(
                filename=target_name,
                content_hash=content_hash,
                chunk_ids=chunk_ids,
                processed_date=current_time,
                reference_number=ref_num,
                policy_number=policy_num,
                title=title,
                version_number=new_version,
                version_sequence=int(new_version.split(".")[0]),
                effective_date=current_time,
                policy_status="ACTIVE",
            )

            # Copy to target container with metadata
            target_client = self.blob_service.get_container_client(target_container)
            target_blob = target_client.get_blob_client(target_name)
            target_blob.upload_blob(
                blob_data, overwrite=True, metadata=state.to_metadata()
            )

            return chunk_ids, superseded_count, version_info

        except Exception:
            # Rollback superseded chunks if upload failed
            if rollback_required:
                logger.error(
                    f"Processing failed for {target_name}. Rolling back superseded chunks..."
                )
                try:
                    self._rollback_superseded_chunks(target_name, old_version)
                except Exception as rollback_error:
                    logger.critical(
                        f"ROLLBACK FAILED for {target_name}: {rollback_error}. "
                        f"Manual intervention required!"
                    )
            raise
        finally:
            # Clean up temp file
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _rollback_superseded_chunks(
        self, source_file: str, restore_version: str
    ) -> int:
        """
        Rollback superseded chunks to ACTIVE status if new version upload fails.

        This prevents data loss during failed version transitions.

        Args:
            source_file: Source PDF filename
            restore_version: Version to restore to ACTIVE status

        Returns:
            Number of chunks restored to ACTIVE

        Raises:
            Exception: If rollback fails (critical error)
        """
        try:
            search_client = self.search_index.get_search_client()

            safe_source = escape_odata_string(source_file)
            results = search_client.search(
                search_text="*",
                filter=f"source_file eq '{safe_source}' and policy_status eq 'SUPERSEDED'",
                select=["id"],
                top=MAX_SEARCH_RESULTS,
            )

            chunks_to_restore = [
                {
                    "id": result["id"],
                    "policy_status": "ACTIVE",
                    "superseded_by": None,
                    "expiration_date": None,
                }
                for result in results
            ]

            if chunks_to_restore:
                search_client.merge_documents(documents=chunks_to_restore)
                logger.info(
                    f"Rolled back {len(chunks_to_restore)} chunks to ACTIVE for {source_file}"
                )
                return len(chunks_to_restore)

            return 0
        except Exception as e:
            logger.error(f"Failed to rollback chunks for {source_file}: {e}")
            raise

    def supersede_old_chunks(
        self, source_file: str, superseded_by: str, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> int:
        """
        Mark old chunks as SUPERSEDED with batched updates.

        This preserves the audit trail for version transitions (v1 → v2).
        Old chunks remain in the index but are filtered out of normal queries.

        Args:
            source_file: The source file whose chunks should be superseded
            superseded_by: The new version number that supersedes these chunks
            batch_size: Number of chunks to update per batch (default: 100)

        Returns:
            Number of chunks marked as SUPERSEDED
        """
        try:
            search_client = self.search_index.get_search_client()
            safe_source = escape_odata_string(source_file)

            results = search_client.search(
                search_text="*",
                filter=f"source_file eq '{safe_source}' and policy_status eq 'ACTIVE'",
                select=["id", "version_number"],
                top=MAX_SEARCH_RESULTS,
            )

            chunks_to_update = []
            total_updated = 0

            for result in results:
                chunks_to_update.append(
                    {
                        "id": result["id"],
                        "policy_status": "SUPERSEDED",
                        "superseded_by": superseded_by,
                        "expiration_date": utc_now_iso(),
                    }
                )

                # Upload in batches to avoid memory buildup
                if len(chunks_to_update) >= batch_size:
                    search_client.merge_documents(documents=chunks_to_update)
                    total_updated += len(chunks_to_update)
                    chunks_to_update = []

            # Upload remaining
            if chunks_to_update:
                search_client.merge_documents(documents=chunks_to_update)
                total_updated += len(chunks_to_update)

            if total_updated > 0:
                logger.info(
                    f"Marked {total_updated} chunks as SUPERSEDED for {source_file}"
                )

            return total_updated

        except Exception as e:
            logger.error(f"Failed to supersede chunks for {source_file}: {e}")
            # Fall back to deletion if update fails
            return self.search_index.delete_by_source_file(source_file)

    def retire_policy(
        self,
        container: str,
        filename: str,
        archive_container: str = "policies-archive",
        sync_batch_id: Optional[str] = None,
    ) -> int:
        """
        Retire a policy with idempotency (safe to call multiple times).

        Unlike delete, this preserves the document for audit purposes.
        If sync_batch_id is provided, archives into dated folder (e.g., "2026-01/").

        Args:
            container: Current container of the policy
            filename: Name of the PDF file
            archive_container: Container for archived policies
            sync_batch_id: Optional date folder in YYYY-MM format

        Returns:
            Number of chunks marked as RETIRED

        Raises:
            ValueError: If sync_batch_id format is invalid
            Exception: For critical errors during retirement
        """
        # Validate sync_batch_id format (YYYY-MM) to prevent path traversal
        if sync_batch_id:
            if not re.match(SYNC_BATCH_ID_PATTERN, sync_batch_id):
                raise ValueError(
                    f"Invalid sync_batch_id format: {sync_batch_id}. Expected YYYY-MM"
                )

            # Additional validation: check if month is valid (01-12)
            try:
                year, month = sync_batch_id.split("-")
                month_int = int(month)
                if not (1 <= month_int <= 12):
                    raise ValueError(
                        f"Invalid month in sync_batch_id: {sync_batch_id}. Month must be 01-12"
                    )
            except (ValueError, AttributeError):
                raise ValueError(
                    f"Invalid sync_batch_id: {sync_batch_id}. Expected YYYY-MM format with valid month (01-12)"
                )

        retired_count = 0
        state = self.get_document_state(container, filename)

        try:
            search_client = self.search_index.get_search_client()

            # Check if already retired (idempotency check)
            safe_filename = escape_odata_string(filename)
            existing = list(
                search_client.search(
                    search_text="*",
                    filter=f"source_file eq '{safe_filename}' and policy_status eq 'RETIRED'",
                    select=["id"],
                    top=1,
                )
            )

            if existing:
                logger.info(f"Policy {filename} already retired, skipping")
                return len(existing)  # Return count of retired chunks

            # Mark all chunks as RETIRED (only ACTIVE or SUPERSEDED chunks)
            results = search_client.search(
                search_text="*",
                filter=f"source_file eq '{safe_filename}' and (policy_status eq 'ACTIVE' or policy_status eq 'SUPERSEDED')",
                select=["id"],
                top=MAX_SEARCH_RESULTS,
            )

            chunks_to_retire = []
            for result in results:
                chunks_to_retire.append(
                    {
                        "id": result["id"],
                        "policy_status": "RETIRED",
                        "expiration_date": utc_now_iso(),
                    }
                )

            if chunks_to_retire:
                search_client.merge_documents(documents=chunks_to_retire)
                retired_count = len(chunks_to_retire)
                logger.info(f"Retired {retired_count} chunks for {filename}")

            # Move PDF to archive container (check if source exists first)
            source_client = self.blob_service.get_container_client(container)
            try:
                source_blob = source_client.get_blob_client(filename)
                source_blob.get_blob_properties()  # Check existence
            except ResourceNotFoundError:
                logger.info(
                    f"Source blob {filename} not found (may already be archived)"
                )
                return (
                    retired_count  # Idempotent: chunks are retired, blob already gone
                )

            archive_client = self.blob_service.get_container_client(archive_container)

            # Ensure archive container exists
            try:
                archive_client.create_container()
            except (ResourceNotFoundError, HttpResponseError) as e:
                if "ContainerAlreadyExists" not in str(e):
                    logger.warning(f"Error creating archive container: {e}")

            # Determine archive blob name (with date folder if provided)
            if sync_batch_id:
                archive_blob_name = f"{sync_batch_id}/{filename}"
            else:
                archive_blob_name = filename

            # Copy to archive
            archive_blob = archive_client.get_blob_client(archive_blob_name)
            blob_data = source_blob.download_blob().readall()

            if state:
                state.policy_status = "RETIRED"
                archive_blob.upload_blob(
                    blob_data, overwrite=True, metadata=state.to_metadata()
                )
            else:
                archive_blob.upload_blob(blob_data, overwrite=True)

            # Delete from active container
            source_blob.delete_blob()
            logger.info(f"Moved {filename} to archive: {archive_blob_name}")

        except Exception as e:
            logger.error(f"Failed to retire policy {filename}: {e}")
            raise  # Re-raise to caller for proper error handling

        return retired_count

    def delete_document(self, container: str, filename: str) -> int:
        """
        Delete a document and its chunks.

        Returns:
            Number of chunks deleted
        """
        # Get chunk IDs from metadata
        state = self.get_document_state(container, filename)
        deleted_count = 0

        if state and state.chunk_ids:
            deleted_count = self.search_index.delete_chunks(state.chunk_ids)

        # Delete the blob
        try:
            container_client = self.blob_service.get_container_client(container)
            container_client.get_blob_client(filename).delete_blob()
            logger.info(f"Deleted blob: {filename}")
        except ResourceNotFoundError:
            logger.debug(f"Blob already deleted: {filename}")
        except HttpResponseError as e:
            logger.warning(f"HTTP error deleting blob {filename}: {e}")

        return deleted_count

    def sync_monthly(
        self,
        source_container: str = SOURCE_CONTAINER,
        target_container: str = TARGET_CONTAINER,
        dry_run: bool = False,
        download_date: Optional[datetime] = None,
        explicit_new_files: Optional[List[Dict[str, Any]]] = None,
        explicit_changed_files: Optional[List[Dict[str, Any]]] = None,
        explicit_deleted_files: Optional[List[str]] = None,
        explicit_total_source_count: Optional[int] = None,
        keep_missing_active: bool = False,
        metadata_gate_mode: str = "fail",
        metadata_report_path: Optional[str] = None,
        quarantine_report_path: Optional[str] = None,
    ) -> SyncReport:
        """
        Perform monthly differential sync with date validation.

        Args:
            source_container: Container with new/updated policies
            target_container: Production container
            dry_run: If True, only detect changes without applying
            download_date: Date when PDFs were downloaded from PolicyTech.
                          If None, uses current date. This is shown to users
                          as "Documents downloaded [date]" in the frontend.
            explicit_new_files: Optional precomputed delta list of new files.
                               Dict format: {"filename": ..., "source_blob": ...}
            explicit_changed_files: Optional precomputed delta list of changed files.
            explicit_deleted_files: Optional precomputed missing/deleted filenames.
            explicit_total_source_count: Optional precomputed source file total.
            keep_missing_active: If True, do not retire deleted/missing files.
            metadata_gate_mode: fail | quarantine | autofill
            metadata_report_path: Optional path for metadata completeness JSON artifact.
            quarantine_report_path: Optional path for quarantined file JSON artifact.

        Returns:
            SyncReport with details of the operation

        Raises:
            ValueError: If download_date is in the future
        """
        sync_date = datetime.now()

        # Validate download_date
        if download_date:
            # Prevent future dates
            if download_date > sync_date:
                raise ValueError(
                    f"download_date cannot be in the future: {download_date}"
                )

            # Prevent unreasonably old dates (e.g., >10 years)
            min_date = datetime(sync_date.year - 10, 1, 1)
            if download_date < min_date:
                logger.warning(
                    f"download_date is very old ({download_date}), using sync_date instead"
                )
                download_date = None

        # Use download_date for display, fall back to sync_date if not provided
        display_date = download_date or sync_date
        sync_batch_id = display_date.strftime("%Y-%m")  # e.g., "2026-01"

        report = SyncReport(
            started_at=sync_date.isoformat(),
            source_container=source_container,
            target_container=target_container,
        )

        print(f"\n{'=' * 60}")
        print(f"POLICY SYNC: {source_container} → {target_container}")
        print(f"{'=' * 60}")

        metadata_rows: List[Dict[str, Any]] = []
        quarantine_rows: List[Dict[str, Any]] = []

        if explicit_new_files is not None or explicit_changed_files is not None:
            # Explicit delta mode (monthly manifest comparison handled by caller).
            new_entries = explicit_new_files or []
            changed_entries = explicit_changed_files or []
            deleted_files = explicit_deleted_files or []
        else:
            # Detect changes directly from source/target containers.
            print("\nDetecting changes...")
            detected_new, detected_changed, detected_deleted = self.detect_changes(
                source_container, target_container
            )
            new_entries = [
                {"filename": name, "source_blob": name} for name in detected_new
            ]
            changed_entries = [
                {"filename": name, "source_blob": name} for name in detected_changed
            ]
            deleted_files = detected_deleted

        report.documents_scanned = (
            len(new_entries) + len(changed_entries) + len(deleted_files)
        )
        report.documents_new = len(new_entries)
        report.documents_changed = len(changed_entries)
        report.documents_deleted = len(deleted_files)

        print(f"  New: {len(new_entries)}")
        print(f"  Changed: {len(changed_entries)}")
        print(f"  Deleted: {len(deleted_files)}")

        if dry_run:
            print("\n[DRY RUN - No changes applied]")
            report.completed_at = datetime.now().isoformat()
            return report

        # Process new documents (v1.0)
        if new_entries:
            print(f"\nProcessing {len(new_entries)} NEW documents (v1.0)...")
            for entry in new_entries:
                filename = entry.get("filename") or entry.get("source_blob") or ""
                source_blob = entry.get("source_blob") or filename
                try:
                    chunk_ids, _, version_info = self.process_document(
                        source_container,
                        target_container,
                        filename,
                        is_update=False,
                        source_blob_name=source_blob,
                        target_filename=filename,
                        metadata_gate_mode=metadata_gate_mode,
                        metadata_report_rows=metadata_rows,
                    )
                    report.chunks_created += len(chunk_ids)
                    print(f"  ✓ {filename} ({len(chunk_ids)} chunks, v1.0)")
                except MetadataValidationError as e:
                    if metadata_gate_mode == "fail":
                        raise
                    report.documents_quarantined += 1
                    quarantine_rows.append(
                        {
                            "filename": filename,
                            "source_blob": source_blob,
                            "change_type": "new",
                            "issues": e.issues,
                            "details": e.details,
                        }
                    )
                    print(f"  ⚠ {filename}: quarantined ({', '.join(e.issues)})")
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")

        # Process changed documents (version transitions: v1 → v2)
        if changed_entries:
            print(
                f"\nProcessing {len(changed_entries)} CHANGED documents (version upgrade)..."
            )
            for entry in changed_entries:
                filename = entry.get("filename") or entry.get("source_blob") or ""
                source_blob = entry.get("source_blob") or filename
                try:
                    chunk_ids, superseded, version_info = self.process_document(
                        source_container,
                        target_container,
                        filename,
                        is_update=True,
                        source_blob_name=source_blob,
                        target_filename=filename,
                        metadata_gate_mode=metadata_gate_mode,
                        metadata_report_rows=metadata_rows,
                    )
                    report.chunks_created += len(chunk_ids)
                    report.chunks_superseded += superseded

                    # Track version transition for audit log
                    if version_info:
                        old_v, new_v = version_info.split("→")
                        report.add_version_transition(filename, old_v, new_v)
                        print(
                            f"  ✓ {filename} (v{old_v} → v{new_v}: {len(chunk_ids)} new, {superseded} superseded)"
                        )
                    else:
                        print(
                            f"  ✓ {filename} ({len(chunk_ids)} new, {superseded} superseded)"
                        )

                except MetadataValidationError as e:
                    if metadata_gate_mode == "fail":
                        raise
                    report.documents_quarantined += 1
                    quarantine_rows.append(
                        {
                            "filename": filename,
                            "source_blob": source_blob,
                            "change_type": "changed",
                            "issues": e.issues,
                            "details": e.details,
                        }
                    )
                    print(f"  ⚠ {filename}: quarantined ({', '.join(e.issues)})")
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")

        # Handle deleted/retired documents
        if deleted_files and not keep_missing_active:
            print(f"\nRETIRING {len(deleted_files)} deleted documents...")
            for filename in deleted_files:
                try:
                    # Use retire instead of delete to preserve audit trail
                    # Pass sync_batch_id for dated archive folder (e.g., "2026-01/")
                    retired = self.retire_policy(
                        target_container, filename, sync_batch_id=sync_batch_id
                    )
                    report.chunks_deleted += retired
                    print(
                        f"  ✓ {filename} ({retired} chunks retired, moved to archive/{sync_batch_id}/)"
                    )
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")
        elif deleted_files and keep_missing_active:
            print(
                f"\nKeeping {len(deleted_files)} missing/deleted files ACTIVE (per configuration)."
            )

        # Calculate unchanged
        if explicit_total_source_count is not None:
            total_source = explicit_total_source_count
        else:
            source_client = self.blob_service.get_container_client(source_container)
            total_source = len(
                [b for b in source_client.list_blobs() if b.name.endswith(".pdf")]
            )
        report.documents_unchanged = max(
            0,
            total_source - report.documents_new - report.documents_changed,
        )

        report.completed_at = datetime.now().isoformat()
        report.log_summary()

        metadata_payload = {
            "generated_at": datetime.now().isoformat(),
            "mode": "sync_monthly",
            "metadata_gate_mode": metadata_gate_mode,
            "total_rows": len(metadata_rows),
            "valid_rows": len([row for row in metadata_rows if row.get("valid")]),
            "invalid_rows": len([row for row in metadata_rows if not row.get("valid")]),
            "rows": metadata_rows,
        }
        quarantine_payload = {
            "generated_at": datetime.now().isoformat(),
            "mode": "sync_monthly",
            "count": len(quarantine_rows),
            "files": quarantine_rows,
        }
        self._write_json_report(metadata_report_path, metadata_payload)
        self._write_json_report(quarantine_report_path, quarantine_payload)

        # Quarantine safeguard: block if documents were quarantined unless
        # the operator has explicitly opted in via environment variable.
        if report.documents_quarantined > 0 and not dry_run:
            msg = (
                f"{report.documents_quarantined} document(s) quarantined — "
                f"these policies are NOT searchable. "
                f"Review quarantine report: {quarantine_report_path or 'N/A'}. "
                f"Set FORCE_QUARANTINE_DEPLOY=true to proceed anyway."
            )
            if not os.environ.get("FORCE_QUARANTINE_DEPLOY"):
                logger.critical(msg)
                raise RuntimeError(msg)
            logger.warning(f"FORCE_QUARANTINE_DEPLOY set. {msg}")

        # Save sync info for frontend display ("Documents downloaded [date]")
        self.save_sync_info(target_container, display_date, sync_batch_id)

        return report

    def sync_delta(
        self,
        *,
        source_container: str,
        target_container: str,
        delta_payload: Dict[str, Any],
        dry_run: bool = False,
        download_date: Optional[datetime] = None,
        metadata_gate_mode: str = "fail",
        metadata_report_path: Optional[str] = None,
        quarantine_report_path: Optional[str] = None,
        keep_missing_active: bool = True,
    ) -> SyncReport:
        """
        Sync explicit delta payload (new + changed) computed by monthly manifest logic.
        """
        new_entries = (
            delta_payload.get("new", []) if isinstance(delta_payload, dict) else []
        )
        changed_entries = (
            delta_payload.get("changed", []) if isinstance(delta_payload, dict) else []
        )
        missing_entries = (
            delta_payload.get("missing", []) if isinstance(delta_payload, dict) else []
        )
        missing_files: List[str] = []
        for entry in missing_entries:
            if isinstance(entry, dict):
                name = str(entry.get("filename") or "").strip()
                if name:
                    missing_files.append(name)
            elif isinstance(entry, str) and entry.strip():
                missing_files.append(entry.strip())
        total_source_count = int(
            delta_payload.get("source_total", len(new_entries) + len(changed_entries))
        )

        return self.sync_monthly(
            source_container=source_container,
            target_container=target_container,
            dry_run=dry_run,
            download_date=download_date,
            explicit_new_files=new_entries,
            explicit_changed_files=changed_entries,
            explicit_deleted_files=missing_files,
            explicit_total_source_count=total_source_count,
            keep_missing_active=keep_missing_active,
            metadata_gate_mode=metadata_gate_mode,
            metadata_report_path=metadata_report_path,
            quarantine_report_path=quarantine_report_path,
        )

    def save_sync_info(
        self, container: str, download_date: datetime, sync_batch_id: str
    ) -> None:
        """
        Save global sync info for frontend access.

        Creates/updates latest_sync_info.json in the target container
        for the frontend to display "Documents downloaded [date]".

        Args:
            container: Target container to store the info file
            download_date: Date when PDFs were downloaded from PolicyTech
            sync_batch_id: Batch ID in format "YYYY-MM"
        """
        info = {
            "last_sync_date": download_date.isoformat(),
            "last_sync_date_display": download_date.strftime("%B %d, %Y"),
            "index_name": getattr(
                self.search_index,
                "index_name",
                os.environ.get("SEARCH_INDEX_NAME", "rush-policies-active"),
            ),
            "sync_batch_id": sync_batch_id,
        }

        try:
            container_client = self.blob_service.get_container_client(container)
            blob_client = container_client.get_blob_client("latest_sync_info.json")
            blob_client.upload_blob(json.dumps(info, indent=2), overwrite=True)
            logger.info(f"Saved sync info: {info['last_sync_date_display']}")
            print(
                f"\n  Sync info saved: Documents downloaded {info['last_sync_date_display']}"
            )
        except Exception as e:
            logger.warning(f"Failed to save sync info: {e}")

    def process_single_document(
        self, pdf_path: str, target_container: str = TARGET_CONTAINER
    ) -> List[str]:
        """
        Process a single local PDF file and upload to index.

        Useful for testing or manual additions.

        Args:
            pdf_path: Path to local PDF file
            target_container: Container to store the blob

        Returns:
            List of created chunk IDs
        """
        filename = os.path.basename(pdf_path)

        # Read file
        with open(pdf_path, "rb") as f:
            content = f.read()

        content_hash = self.compute_content_hash(content)

        # Check if already exists
        existing_state = self.get_document_state(target_container, filename)
        if existing_state and existing_state.content_hash == content_hash:
            print(f"Document {filename} unchanged, skipping")
            return existing_state.chunk_ids

        # Delete existing chunks if updating
        if existing_state:
            self.search_index.delete_chunks(existing_state.chunk_ids)

        # Process
        chunks = self.chunker.process_pdf(pdf_path)
        for chunk in chunks:
            chunk.source_file = filename

        # Upload to index
        if chunks:
            self.search_index.upload_chunks(chunks)

        chunk_ids = [c.chunk_id for c in chunks]

        # Upload blob with metadata
        state = DocumentState(
            filename=filename,
            content_hash=content_hash,
            chunk_ids=chunk_ids,
            processed_date=datetime.now().isoformat(),
            reference_number=chunks[0].reference_number if chunks else "",
            policy_number=chunks[0].policy_number if chunks else "",
            title=chunks[0].policy_title if chunks else "",
        )

        container_client = self.blob_service.get_container_client(target_container)
        blob_client = container_client.get_blob_client(filename)
        blob_client.upload_blob(content, overwrite=True, metadata=state.to_metadata())

        print(f"✓ Processed {filename}: {len(chunks)} chunks")
        return chunk_ids

    def reindex_all(self, container: str = TARGET_CONTAINER) -> SyncReport:
        """
        Full reindex of all documents in a container.

        WARNING: This is slow for large document sets. Use sync_monthly for incremental updates.
        """
        report = SyncReport(
            started_at=datetime.now().isoformat(),
            source_container=container,
            target_container=container,
        )

        print(f"\n{'=' * 60}")
        print(f"FULL REINDEX: {container}")
        print(f"{'=' * 60}")

        container_client = self.blob_service.get_container_client(container)
        pdf_blobs = [
            b for b in container_client.list_blobs() if b.name.endswith(".pdf")
        ]

        print(f"Found {len(pdf_blobs)} PDF documents")
        report.documents_scanned = len(pdf_blobs)

        for i, blob in enumerate(pdf_blobs, 1):
            filename = blob.name
            print(f"\n[{i}/{len(pdf_blobs)}] Processing {filename}...")

            try:
                # Download
                content = (
                    container_client.get_blob_client(filename).download_blob().readall()
                )
                content_hash = self.compute_content_hash(content)

                # Delete existing chunks
                existing_state = self.get_document_state(container, filename)
                if existing_state and existing_state.chunk_ids:
                    deleted = self.search_index.delete_chunks(existing_state.chunk_ids)
                    report.chunks_deleted += deleted

                # Save temp and process
                tmp_path = None
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    chunks = self.chunker.process_pdf(tmp_path)
                    for chunk in chunks:
                        chunk.source_file = filename

                    if chunks:
                        self.search_index.upload_chunks(chunks)
                        report.chunks_created += len(chunks)

                    # Update metadata
                    state = DocumentState(
                        filename=filename,
                        content_hash=content_hash,
                        chunk_ids=[c.chunk_id for c in chunks],
                        processed_date=datetime.now().isoformat(),
                        reference_number=chunks[0].reference_number if chunks else "",
                        policy_number=chunks[0].policy_number if chunks else "",
                        title=chunks[0].policy_title if chunks else "",
                    )

                    blob_client = container_client.get_blob_client(filename)
                    blob_client.set_blob_metadata(state.to_metadata())

                    print(f"  ✓ {len(chunks)} chunks")
                    report.documents_new += 1

                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            except Exception as e:
                report.errors.append({"file": filename, "error": str(e)})
                print(f"  ✗ Error: {e}")

        report.completed_at = datetime.now().isoformat()
        report.log_summary()

        return report


# CLI for testing
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("POLICY SYNC MANAGER")
    print("=" * 60)

    if not STORAGE_CONNECTION_STRING:
        print("ERROR: STORAGE_CONNECTION_STRING not set")
        sys.exit(1)

    # Helper to parse CLI args
    def get_arg(flag, default=None):
        """Get argument value from sys.argv."""
        for i, arg in enumerate(sys.argv):
            if arg == flag and i + 1 < len(sys.argv):
                return sys.argv[i + 1]
            if arg.startswith(f"{flag}="):
                return arg.split("=", 1)[1]
        return default

    # Parse backend option from command line
    backend = get_arg("--backend")
    use_docling = "--use-docling" in sys.argv
    no_docling = "--no-docling" in sys.argv
    if no_docling:
        use_docling = False

    sync = PolicySyncManager(
        backend=backend, use_docling=use_docling if use_docling or no_docling else None
    )
    print(f"Backend: {sync.chunker.backend}")

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "detect":
            # Just detect changes without applying
            source = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else SOURCE_CONTAINER
            )
            target = (
                sys.argv[3]
                if len(sys.argv) > 3 and not sys.argv[3].startswith("--")
                else TARGET_CONTAINER
            )

            new, changed, deleted = sync.detect_changes(source, target)
            print(f"\nNew: {len(new)}")
            for f in new[:10]:
                print(f"  + {f}")
            if len(new) > 10:
                print(f"  ... and {len(new) - 10} more")
            print(f"\nChanged: {len(changed)}")
            for f in changed[:10]:
                print(f"  ~ {f}")
            if len(changed) > 10:
                print(f"  ... and {len(changed) - 10} more")
            print(f"\nDeleted: {len(deleted)}")
            for f in deleted[:10]:
                print(f"  - {f}")
            if len(deleted) > 10:
                print(f"  ... and {len(deleted) - 10} more")

            # Summary
            total_source = len(new) + len(changed)
            print(f"\n{'=' * 40}")
            print(f"Total in source: {total_source + len(deleted) - len(deleted)}")
            print("Unchanged: (run sync to calculate)")

        elif command == "sync":
            source = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else SOURCE_CONTAINER
            )
            target = (
                sys.argv[3]
                if len(sys.argv) > 3 and not sys.argv[3].startswith("--")
                else TARGET_CONTAINER
            )
            dry_run = "--dry-run" in sys.argv
            metadata_gate_mode = get_arg("--metadata-gate-mode", "fail")
            metadata_report_path = get_arg("--metadata-report")
            quarantine_report_path = get_arg("--quarantine-report")
            keep_missing_active = "--keep-missing-active" in sys.argv

            # Parse optional version and effective-date
            version = get_arg("--version")
            effective_date = get_arg("--effective-date")

            if version or effective_date:
                print("\nNote: --version and --effective-date are advisory.")
                print("  Version transitions are auto-incremented (v1.0 → v2.0)")
                if version:
                    print(f"  Requested version: {version}")
                if effective_date:
                    print(f"  Effective date: {effective_date}")

            sync.sync_monthly(
                source,
                target,
                dry_run=dry_run,
                metadata_gate_mode=metadata_gate_mode,
                metadata_report_path=metadata_report_path,
                quarantine_report_path=quarantine_report_path,
                keep_missing_active=keep_missing_active,
            )

        elif command == "sync-delta":
            source = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else SOURCE_CONTAINER
            )
            target = (
                sys.argv[3]
                if len(sys.argv) > 3 and not sys.argv[3].startswith("--")
                else TARGET_CONTAINER
            )
            dry_run = "--dry-run" in sys.argv
            delta_file = get_arg("--delta-file")
            if not delta_file:
                print("ERROR: sync-delta requires --delta-file <path>")
                sys.exit(1)

            metadata_gate_mode = get_arg("--metadata-gate-mode", "fail")
            metadata_report_path = get_arg("--metadata-report")
            quarantine_report_path = get_arg("--quarantine-report")
            keep_missing_active = "--keep-missing-active" in sys.argv

            with open(delta_file, "r", encoding="utf-8") as f:
                delta_payload = json.load(f)

            sync.sync_delta(
                source_container=source,
                target_container=target,
                delta_payload=delta_payload,
                dry_run=dry_run,
                metadata_gate_mode=metadata_gate_mode,
                metadata_report_path=metadata_report_path,
                quarantine_report_path=quarantine_report_path,
                keep_missing_active=keep_missing_active,
            )

        elif command == "reindex":
            container = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else TARGET_CONTAINER
            )
            sync.reindex_all(container)

        elif command == "process" and len(sys.argv) > 2:
            pdf_path = sys.argv[2]
            sync.process_single_document(pdf_path)

        elif command == "rollback":
            # Rollback a policy to a previous version
            ref_number = get_arg("--reference") or get_arg("--ref")
            to_version = get_arg("--to-version")
            reason = get_arg("--reason") or "Manual rollback"

            if not ref_number or not to_version:
                print("ERROR: Rollback requires --reference and --to-version")
                print("\nUsage:")
                print(
                    "  python policy_sync.py rollback --reference POL-001 --to-version 1.0 --reason 'Issue found'"
                )
                sys.exit(1)

            print(f"\nRolling back {ref_number} to version {to_version}")
            print(f"Reason: {reason}")

            try:
                search_client = sync.search_index.get_search_client()
                safe_ref = escape_odata_string(ref_number)
                safe_to_ver = escape_odata_string(to_version)

                # Find current active version
                current_results = list(
                    search_client.search(
                        search_text="*",
                        filter=f"reference_number eq '{safe_ref}' and policy_status eq 'ACTIVE'",
                        select=["id", "version_number", "source_file"],
                        top=1,
                    )
                )

                if not current_results:
                    print(f"ERROR: No ACTIVE chunks found for {ref_number}")
                    sys.exit(1)

                current_version = current_results[0].get("version_number", "unknown")
                source_file = current_results[0].get("source_file", "")
                print(f"  Current version: {current_version}")

                # Find target version chunks
                target_results = list(
                    search_client.search(
                        search_text="*",
                        filter=f"reference_number eq '{safe_ref}' and version_number eq '{safe_to_ver}'",
                        select=["id", "policy_status"],
                        top=1000,
                    )
                )

                if not target_results:
                    print(f"ERROR: No chunks found for version {to_version}")
                    sys.exit(1)

                print(f"  Found {len(target_results)} chunks for version {to_version}")

                # Mark current version as SUPERSEDED
                current_chunks = list(
                    search_client.search(
                        search_text="*",
                        filter=f"reference_number eq '{safe_ref}' and policy_status eq 'ACTIVE'",
                        select=["id"],
                        top=1000,
                    )
                )

                supersede_updates = [
                    {
                        "id": c["id"],
                        "policy_status": "SUPERSEDED",
                        "superseded_by": to_version,
                    }
                    for c in current_chunks
                ]
                if supersede_updates:
                    search_client.merge_documents(documents=supersede_updates)
                    print(
                        f"  ✓ Marked {len(supersede_updates)} v{current_version} chunks as SUPERSEDED"
                    )

                # Mark target version as ACTIVE
                activate_updates = [
                    {"id": c["id"], "policy_status": "ACTIVE", "superseded_by": None}
                    for c in target_results
                ]
                search_client.merge_documents(documents=activate_updates)
                print(
                    f"  ✓ Marked {len(activate_updates)} v{to_version} chunks as ACTIVE"
                )

                print(
                    f"\n✓ Rollback complete: {ref_number} v{current_version} → v{to_version}"
                )
                print(f"  Reason logged: {reason}")

            except Exception as e:
                print(f"ERROR: Rollback failed: {e}")
                sys.exit(1)

        elif command == "retire":
            # Retire a policy
            filename = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else None
            )
            container = get_arg("--container") or TARGET_CONTAINER

            if not filename:
                print("ERROR: Retire requires a filename")
                print("\nUsage:")
                print(
                    "  python policy_sync.py retire policy.pdf [--container policies-active]"
                )
                sys.exit(1)

            print(f"\nRetiring: {filename}")
            retired_count = sync.retire_policy(container, filename)
            print(f"✓ Retired {retired_count} chunks, moved to archive")

        elif command == "validate":
            # Validate hash consistency
            container = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
                else TARGET_CONTAINER
            )
            sample = int(get_arg("--sample") or "10")

            print(f"\nValidating hash consistency in {container}...")
            print(f"Sample size: {sample}")

            results = sync.validate_hash_consistency(container, sample_size=sample)

            print("\nResults:")
            print(f"  Documents checked: {results['total_checked']}")
            print(f"  Hashes match:      {results['matches']}")
            print(f"  Mismatches:        {len(results['mismatches'])}")
            print(f"  Errors:            {len(results['errors'])}")

            if results["mismatches"]:
                print("\nMismatches:")
                for mm in results["mismatches"][:5]:
                    print(f"  - {mm['filename']}: {mm.get('issue', 'Hash mismatch')}")
                if len(results["mismatches"]) > 5:
                    print(f"  ... and {len(results['mismatches']) - 5} more")

            if results["errors"]:
                print("\nErrors:")
                for err in results["errors"][:3]:
                    if isinstance(err, dict):
                        print(
                            f"  - {err.get('filename', 'unknown')}: {err.get('error', 'unknown')}"
                        )
                    else:
                        print(f"  - {err}")

            if (
                results["matches"] == results["total_checked"]
                and results["total_checked"] > 0
            ):
                print("\n✓ All hashes valid - safe to sync")
            elif results["mismatches"]:
                print("\n⚠ Hash mismatches detected - review before syncing")

        else:
            print("Usage:")
            print(
                "  python policy_sync.py detect [source] [target]    # Detect changes"
            )
            print(
                "  python policy_sync.py sync [source] [target]      # Run monthly sync"
            )
            print(
                "  python policy_sync.py sync-delta [source] [target] --delta-file <json>"
            )
            print(
                "  python policy_sync.py validate [container]        # Validate hash consistency"
            )
            print("  python policy_sync.py reindex [container]         # Full reindex")
            print(
                "  python policy_sync.py process <pdf_path>          # Process single file"
            )
            print("  python policy_sync.py rollback --reference REF --to-version X.X")
            print(
                "  python policy_sync.py retire <filename>           # Retire a policy"
            )
            print("\nOptions:")
            print("  --backend=docling   Use Docling parser")
            print("  --backend=pymupdf   Use PyMuPDF parser")
            print("  --use-docling       Shorthand for --backend=docling")
            print("  --no-docling        Shorthand for --backend=pymupdf")
            print("  --dry-run           Detect changes only (for sync)")
            print("  --metadata-gate-mode fail|quarantine|autofill")
            print(
                "  --metadata-report <path>      Write metadata completeness artifact"
            )
            print("  --quarantine-report <path>    Write quarantined files artifact")
            print(
                "  --keep-missing-active         Skip retire/delete for missing files"
            )
            print("  --version X.X       Advisory version number (auto-increments)")
            print("  --effective-date    Advisory effective date (uses current time)")
            print("  --reason 'text'     Reason for rollback")
    else:
        print(
            "\nRun with 'detect', 'sync', 'sync-delta', 'reindex', 'process', 'rollback', or 'retire' command"
        )
