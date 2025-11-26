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

import os
import json
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

# Configure logging
logger = logging.getLogger(__name__)

# Maximum size for blob metadata (8KB with buffer)
MAX_METADATA_SIZE = 7500

from preprocessing.chunker import PolicyChunker, PolicyChunk
from azure_policy_index import PolicySearchIndex


# Configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
SOURCE_CONTAINER = os.environ.get("SOURCE_CONTAINER", "policy-monthly")
TARGET_CONTAINER = os.environ.get("CONTAINER_NAME", "policies-active")


@dataclass
class DocumentState:
    """Tracks the state of a document for sync purposes."""
    filename: str
    content_hash: str
    chunk_ids: List[str] = field(default_factory=list)
    processed_date: str = ""
    reference_number: str = ""
    title: str = ""

    def to_metadata(self) -> Dict[str, str]:
        """Convert to blob metadata format (all values must be strings)."""
        chunk_ids_json = json.dumps(self.chunk_ids)

        # Validate size (8KB limit for Azure Blob metadata)
        if len(chunk_ids_json) > MAX_METADATA_SIZE:
            # Store count only, rely on search index for IDs
            chunk_ids_json = json.dumps({"count": len(self.chunk_ids), "truncated": True})
            logger.warning(f"Metadata truncated for {self.filename}: {len(self.chunk_ids)} chunks")

        return {
            "content_hash": self.content_hash,
            "chunk_ids": chunk_ids_json,
            "processed_date": self.processed_date,
            "reference_number": self.reference_number,
            "title": self.title,
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

        return cls(
            filename=filename,
            content_hash=metadata.get("content_hash", ""),
            chunk_ids=chunk_ids,
            processed_date=metadata.get("processed_date", ""),
            reference_number=metadata.get("reference_number", ""),
            title=metadata.get("title", ""),
        )


@dataclass
class SyncReport:
    """Report of sync operation results."""
    started_at: str
    completed_at: str = ""
    source_container: str = ""
    target_container: str = ""
    documents_scanned: int = 0
    documents_new: int = 0
    documents_changed: int = 0
    documents_unchanged: int = 0
    documents_deleted: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    errors: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def log_summary(self):
        """Log human-readable summary."""
        summary = f"""
{'=' * 60}
SYNC REPORT
{'=' * 60}
Started: {self.started_at}
Completed: {self.completed_at}
Source: {self.source_container}
Target: {self.target_container}
{'-' * 60}
Documents scanned: {self.documents_scanned}
  New: {self.documents_new}
  Changed: {self.documents_changed}
  Unchanged: {self.documents_unchanged}
  Deleted: {self.documents_deleted}
{'-' * 60}
Chunks created: {self.chunks_created}
Chunks deleted: {self.chunks_deleted}"""

        if self.errors:
            summary += f"\nErrors: {len(self.errors)}"
            for err in self.errors[:5]:
                summary += f"\n  - {err['file']}: {err['error']}"

        logger.info(summary)


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
        use_docling: Optional[bool] = None,  # Deprecated, kept for backward compatibility
        backend: Optional[str] = None,  # Deprecated, kept for backward compatibility
    ):
        self.blob_service = BlobServiceClient.from_connection_string(connection_string)

        # Initialize chunker (always uses Docling now)
        self.chunker = chunker or PolicyChunker(max_chunk_size=1500)

        self.search_index = search_index or PolicySearchIndex()
        logger.info(f"PolicySyncManager initialized with Docling backend")

    def compute_content_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of document content."""
        return hashlib.sha256(content).hexdigest()

    def compute_content_hash_streaming(self, blob_client: BlobClient) -> str:
        """
        Compute SHA-256 hash using streaming to avoid memory explosion.

        For 1800 docs × 5MB = 9GB if loaded all at once.
        Streaming processes one chunk at a time.
        """
        hash_obj = hashlib.sha256()
        download_stream = blob_client.download_blob()
        for chunk in download_stream.chunks():
            hash_obj.update(chunk)
        return hash_obj.hexdigest()

    def get_document_state(self, container: str, filename: str) -> Optional[DocumentState]:
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
        self,
        source_container: str,
        target_container: str
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
            if blob.name.endswith('.pdf'):
                # Use streaming hash to avoid loading entire PDF into memory
                blob_client = source_client.get_blob_client(blob.name)
                source_files[blob.name] = self.compute_content_hash_streaming(blob_client)

        # Get all files in target with their hashes
        target_files = {}
        for blob in target_client.list_blobs(include=['metadata']):
            if blob.name.endswith('.pdf'):
                metadata = blob.metadata or {}
                target_files[blob.name] = metadata.get('content_hash', '')

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

    def process_document(
        self,
        source_container: str,
        target_container: str,
        filename: str,
        is_update: bool = False
    ) -> Tuple[List[str], int]:
        """
        Process a single document: chunk, embed, upload, and copy.

        Args:
            source_container: Container with the source PDF
            target_container: Container to copy processed PDF
            filename: Name of the PDF file
            is_update: If True, delete existing chunks first

        Returns:
            Tuple of (chunk_ids, deleted_count)
        """
        deleted_count = 0

        # If updating, delete old chunks first
        if is_update:
            deleted_count = self.search_index.delete_by_source_file(filename)

        # Download PDF from source
        source_client = self.blob_service.get_container_client(source_container)
        blob_data = source_client.get_blob_client(filename).download_blob().readall()
        content_hash = self.compute_content_hash(blob_data)

        # Save temporarily for processing
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(blob_data)
            tmp_path = tmp.name

        try:
            # Chunk the document
            chunks = self.chunker.process_pdf(tmp_path)

            # Update source_file to use original filename
            for chunk in chunks:
                chunk.source_file = filename

            # Upload chunks to search index
            if chunks:
                self.search_index.upload_chunks(chunks)

            chunk_ids = [c.chunk_id for c in chunks]

            # Get metadata from first chunk (if available)
            ref_num = chunks[0].reference_number if chunks else ""
            title = chunks[0].policy_title if chunks else ""

            # Create document state
            state = DocumentState(
                filename=filename,
                content_hash=content_hash,
                chunk_ids=chunk_ids,
                processed_date=datetime.now().isoformat(),
                reference_number=ref_num,
                title=title,
            )

            # Copy to target container with metadata
            target_client = self.blob_service.get_container_client(target_container)
            target_blob = target_client.get_blob_client(filename)
            target_blob.upload_blob(
                blob_data,
                overwrite=True,
                metadata=state.to_metadata()
            )

            return chunk_ids, deleted_count

        finally:
            # Clean up temp file
            os.unlink(tmp_path)

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
        dry_run: bool = False
    ) -> SyncReport:
        """
        Perform monthly differential sync.

        Args:
            source_container: Container with new/updated policies
            target_container: Production container
            dry_run: If True, only detect changes without applying

        Returns:
            SyncReport with details of the operation
        """
        report = SyncReport(
            started_at=datetime.now().isoformat(),
            source_container=source_container,
            target_container=target_container,
        )

        print(f"\n{'=' * 60}")
        print(f"POLICY SYNC: {source_container} → {target_container}")
        print(f"{'=' * 60}")

        # Detect changes
        print("\nDetecting changes...")
        new_files, changed_files, deleted_files = self.detect_changes(
            source_container, target_container
        )

        report.documents_scanned = len(new_files) + len(changed_files) + len(deleted_files)
        report.documents_new = len(new_files)
        report.documents_changed = len(changed_files)
        report.documents_deleted = len(deleted_files)

        print(f"  New: {len(new_files)}")
        print(f"  Changed: {len(changed_files)}")
        print(f"  Deleted: {len(deleted_files)}")

        if dry_run:
            print("\n[DRY RUN - No changes applied]")
            report.completed_at = datetime.now().isoformat()
            return report

        # Process new documents
        if new_files:
            print(f"\nProcessing {len(new_files)} new documents...")
            for filename in new_files:
                try:
                    chunk_ids, _ = self.process_document(
                        source_container, target_container, filename, is_update=False
                    )
                    report.chunks_created += len(chunk_ids)
                    print(f"  ✓ {filename} ({len(chunk_ids)} chunks)")
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")

        # Process changed documents
        if changed_files:
            print(f"\nProcessing {len(changed_files)} changed documents...")
            for filename in changed_files:
                try:
                    chunk_ids, deleted = self.process_document(
                        source_container, target_container, filename, is_update=True
                    )
                    report.chunks_created += len(chunk_ids)
                    report.chunks_deleted += deleted
                    print(f"  ✓ {filename} ({len(chunk_ids)} new, {deleted} deleted)")
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")

        # Handle deleted documents
        if deleted_files:
            print(f"\nRemoving {len(deleted_files)} deleted documents...")
            for filename in deleted_files:
                try:
                    deleted = self.delete_document(target_container, filename)
                    report.chunks_deleted += deleted
                    print(f"  ✓ {filename} ({deleted} chunks removed)")
                except Exception as e:
                    report.errors.append({"file": filename, "error": str(e)})
                    print(f"  ✗ {filename}: {e}")

        # Calculate unchanged
        source_client = self.blob_service.get_container_client(source_container)
        total_source = len([b for b in source_client.list_blobs() if b.name.endswith('.pdf')])
        report.documents_unchanged = total_source - report.documents_new - report.documents_changed

        report.completed_at = datetime.now().isoformat()
        report.log_summary()

        return report

    def process_single_document(
        self,
        pdf_path: str,
        target_container: str = TARGET_CONTAINER
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
        with open(pdf_path, 'rb') as f:
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
        pdf_blobs = [b for b in container_client.list_blobs() if b.name.endswith('.pdf')]

        print(f"Found {len(pdf_blobs)} PDF documents")
        report.documents_scanned = len(pdf_blobs)

        for i, blob in enumerate(pdf_blobs, 1):
            filename = blob.name
            print(f"\n[{i}/{len(pdf_blobs)}] Processing {filename}...")

            try:
                # Download
                content = container_client.get_blob_client(filename).download_blob().readall()
                content_hash = self.compute_content_hash(content)

                # Delete existing chunks
                existing_state = self.get_document_state(container, filename)
                if existing_state and existing_state.chunk_ids:
                    deleted = self.search_index.delete_chunks(existing_state.chunk_ids)
                    report.chunks_deleted += deleted

                # Save temp and process
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
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
                        title=chunks[0].policy_title if chunks else "",
                    )

                    blob_client = container_client.get_blob_client(filename)
                    blob_client.set_blob_metadata(state.to_metadata())

                    print(f"  ✓ {len(chunks)} chunks")
                    report.documents_new += 1

                finally:
                    os.unlink(tmp_path)

            except Exception as e:
                report.errors.append({"file": filename, "error": str(e)})
                print(f"  ✗ Error: {e}")

        report.completed_at = datetime.now().isoformat()
        report.print_summary()

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

    # Parse backend option from command line
    backend = None
    use_docling = None
    for arg in sys.argv:
        if arg.startswith('--backend='):
            backend = arg.split('=')[1]
        elif arg == '--use-docling':
            use_docling = True
        elif arg == '--no-docling':
            use_docling = False

    sync = PolicySyncManager(backend=backend, use_docling=use_docling)
    print(f"Backend: {sync.chunker.backend}")

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "detect":
            # Just detect changes without applying
            source = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else SOURCE_CONTAINER
            target = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith('--') else TARGET_CONTAINER

            new, changed, deleted = sync.detect_changes(source, target)
            print(f"\nNew: {len(new)}")
            for f in new[:10]:
                print(f"  + {f}")
            print(f"\nChanged: {len(changed)}")
            for f in changed[:10]:
                print(f"  ~ {f}")
            print(f"\nDeleted: {len(deleted)}")
            for f in deleted[:10]:
                print(f"  - {f}")

        elif command == "sync":
            source = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else SOURCE_CONTAINER
            target = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith('--') else TARGET_CONTAINER
            dry_run = "--dry-run" in sys.argv

            sync.sync_monthly(source, target, dry_run=dry_run)

        elif command == "reindex":
            container = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else TARGET_CONTAINER
            sync.reindex_all(container)

        elif command == "process" and len(sys.argv) > 2:
            pdf_path = sys.argv[2]
            sync.process_single_document(pdf_path)

        else:
            print("Usage:")
            print("  python policy_sync.py detect [source] [target]    # Detect changes")
            print("  python policy_sync.py sync [source] [target]      # Run monthly sync")
            print("  python policy_sync.py reindex [container]         # Full reindex")
            print("  python policy_sync.py process <pdf_path>          # Process single file")
            print("\nOptions:")
            print("  --backend=docling   Use Docling parser")
            print("  --backend=pymupdf   Use PyMuPDF parser")
            print("  --use-docling       Shorthand for --backend=docling")
            print("  --no-docling        Shorthand for --backend=pymupdf")
            print("  --dry-run           Detect changes only (for sync)")
    else:
        print("\nRun with 'detect', 'sync', 'reindex', or 'process' command")
