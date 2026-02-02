#!/usr/bin/env python3
"""
Local Folder Checkpointed Ingestion - January 2026 Fresh Start

Processes PDFs from a local folder with checkpoint saves every 100 documents.
Supports fresh start (delete old blobs + clear index) and resume from checkpoint.

Usage:
    # Fresh start (Jan 2026 batch)
    python scripts/local_folder_ingest.py \
        --folder "SimpleExport_27236_2026_01_22" \
        --batch-size 100 \
        --workers 8

    # Resume from checkpoint
    python scripts/local_folder_ingest.py --resume

    # Check status
    python scripts/local_folder_ingest.py --status

    # Dry run
    python scripts/local_folder_ingest.py --folder "..." --dry-run
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

from azure.storage.blob import BlobServiceClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient


# Checkpoint file location
CHECKPOINT_FILE = Path(__file__).parent.parent / "data" / "local_ingest_checkpoint.json"


@dataclass
class LocalCheckpoint:
    """Checkpoint for local folder ingestion."""
    batch_id: str
    source_folder: str
    started_at: str
    last_updated: str
    phase: str  # "init", "blob_cleanup", "blob_upload", "index_clear", "processing", "completed"
    total_documents: int = 0
    completed_count: int = 0
    failed_count: int = 0
    total_chunks: int = 0
    blobs_deleted: bool = False
    blobs_uploaded: bool = False
    index_cleared: bool = False
    documents: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LocalCheckpoint":
        return cls(**data)

    def save(self, path: Path = CHECKPOINT_FILE):
        """Save checkpoint to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.last_updated = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path = CHECKPOINT_FILE) -> Optional["LocalCheckpoint"]:
        """Load checkpoint from file if exists."""
        if path.exists():
            with open(path) as f:
                return cls.from_dict(json.load(f))
        return None

    def get_pending_documents(self) -> List[str]:
        """Get list of documents not yet completed."""
        pending = []
        for filename, doc in self.documents.items():
            if doc["status"] in ("pending", "processing"):
                pending.append(filename)
        return pending

    def mark_completed(self, filename: str, chunks: int, time_ms: float):
        """Mark a document as completed."""
        if filename in self.documents:
            self.documents[filename]["status"] = "completed"
            self.documents[filename]["chunks_created"] = chunks
            self.documents[filename]["processing_time_ms"] = time_ms
            self.documents[filename]["completed_at"] = datetime.now().isoformat()
            self.completed_count += 1
            self.total_chunks += chunks

    def mark_failed(self, filename: str, error: str):
        """Mark a document as failed."""
        if filename in self.documents:
            self.documents[filename]["status"] = "failed"
            self.documents[filename]["error"] = error
            self.failed_count += 1


class LocalFolderPipeline:
    """Pipeline for ingesting PDFs from a local folder with checkpointing."""

    def __init__(self, workers: int = 8, batch_size: int = 100):
        # Azure Storage
        self.storage_conn_str = os.getenv("STORAGE_CONNECTION_STRING")
        self.container_name = os.getenv("CONTAINER_NAME", "policies-active")

        # Azure Search
        self.search_endpoint = os.getenv("SEARCH_ENDPOINT")
        self.search_api_key = os.getenv("SEARCH_API_KEY")
        self.index_name = "rush-policies"

        # Parallelization settings
        self.workers = workers
        self.batch_size = batch_size

        # Validate config
        if not self.storage_conn_str:
            raise ValueError("STORAGE_CONNECTION_STRING not set")
        if not self.search_endpoint or not self.search_api_key:
            raise ValueError("SEARCH_ENDPOINT and SEARCH_API_KEY must be set")

        # Initialize clients
        self.blob_service = BlobServiceClient.from_connection_string(self.storage_conn_str)
        self.container_client = self.blob_service.get_container_client(self.container_name)

        credential = AzureKeyCredential(self.search_api_key)
        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name=self.index_name,
            credential=credential
        )
        self.index_client = SearchIndexClient(
            endpoint=self.search_endpoint,
            credential=credential
        )

        # Lazy load chunker
        self._chunker = None
        self._search_index = None

    @property
    def chunker(self):
        """Lazy load the chunker."""
        if self._chunker is None:
            print("Loading Docling chunker...")
            from preprocessing.chunker import PolicyChunker
            self._chunker = PolicyChunker()
            print("Chunker loaded.")
        return self._chunker

    @property
    def search_index(self):
        """Lazy load the search index."""
        if self._search_index is None:
            from azure_policy_index import PolicySearchIndex
            self._search_index = PolicySearchIndex()
        return self._search_index

    def list_local_pdfs(self, folder: Path) -> List[Path]:
        """List all PDF files in the folder (skip non-PDFs)."""
        pdfs = []
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() == '.pdf':
                pdfs.append(f)
        return sorted(pdfs, key=lambda p: p.name)

    def clear_blob_container(self) -> int:
        """Delete all blobs from policies-active container."""
        print(f"\nDeleting all blobs from '{self.container_name}'...")
        deleted = 0
        blobs = list(self.container_client.list_blobs())
        total = len(blobs)

        for i, blob in enumerate(blobs):
            self.container_client.delete_blob(blob.name)
            deleted += 1
            if deleted % 100 == 0 or deleted == total:
                print(f"  Deleted {deleted}/{total} blobs...")

        print(f"Deleted {deleted} blobs from '{self.container_name}'")
        return deleted

    def upload_pdfs_to_blob(self, folder: Path) -> int:
        """Upload all PDFs from folder to policies-active container."""
        print(f"\nUploading PDFs from '{folder}' to '{self.container_name}'...")
        pdfs = self.list_local_pdfs(folder)
        total = len(pdfs)
        uploaded = 0

        for i, pdf_path in enumerate(pdfs):
            blob_client = self.container_client.get_blob_client(pdf_path.name)
            with open(pdf_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)
            uploaded += 1
            if uploaded % 100 == 0 or uploaded == total:
                print(f"  Uploaded {uploaded}/{total} PDFs...")

        print(f"Uploaded {uploaded} PDFs to '{self.container_name}'")
        return uploaded

    def clear_index(self) -> float:
        """Delete all documents from the search index."""
        print(f"\nClearing index '{self.index_name}'...")
        start = time.perf_counter()

        total_deleted = 0
        while True:
            results = self.search_client.search(
                search_text="*",
                select=["id"],
                top=1000
            )
            doc_ids = [r["id"] for r in results]

            if not doc_ids:
                break

            documents = [{"id": doc_id} for doc_id in doc_ids]
            self.search_client.delete_documents(documents)
            total_deleted += len(doc_ids)
            print(f"  Deleted {total_deleted} documents...")

        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"Cleared {total_deleted} documents in {elapsed_ms:.0f}ms")
        return elapsed_ms

    def process_single_pdf(self, pdf_path: Path) -> Dict:
        """Process a single PDF file. Returns result dict."""
        result = {
            "filename": pdf_path.name,
            "status": "completed",
            "chunks_created": 0,
            "processing_time_ms": 0.0,
            "error": None
        }
        start = time.perf_counter()

        try:
            # Process with Docling
            chunks = self.chunker.process_pdf(str(pdf_path))
            result["chunks_created"] = len(chunks)

            # Index chunks
            if chunks:
                documents = [chunk.to_azure_document() for chunk in chunks]
                batch_size = 100
                for i in range(0, len(documents), batch_size):
                    batch = documents[i:i + batch_size]
                    self.search_client.upload_documents(batch)

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        result["processing_time_ms"] = (time.perf_counter() - start) * 1000
        return result

    def process_batch(self, pdf_paths: List[Path], checkpoint: LocalCheckpoint) -> int:
        """Process a batch of PDFs with parallel workers."""
        processed = 0

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self.process_single_pdf, pdf): pdf
                for pdf in pdf_paths
            }

            for future in as_completed(futures):
                pdf_path = futures[future]
                try:
                    result = future.result()

                    if result["status"] == "completed":
                        checkpoint.mark_completed(
                            pdf_path.name,
                            result["chunks_created"],
                            result["processing_time_ms"]
                        )
                        print(f"  [OK] {pdf_path.name[:50]} ({result['chunks_created']} chunks, {result['processing_time_ms']:.0f}ms)")
                    else:
                        checkpoint.mark_failed(pdf_path.name, result["error"])
                        print(f"  [FAIL] {pdf_path.name[:50]}: {result['error'][:80]}")

                except Exception as e:
                    checkpoint.mark_failed(pdf_path.name, str(e))
                    print(f"  [FAIL] {pdf_path.name[:50]}: {e}")

                processed += 1

        return processed

    def run(
        self,
        folder: Optional[Path] = None,
        fresh: bool = False,
        resume: bool = False,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> LocalCheckpoint:
        """Run the pipeline with checkpoint support."""

        print(f"\n{'='*60}")
        print("LOCAL FOLDER CHECKPOINTED INGESTION")
        print(f"{'='*60}")
        print(f"Workers: {self.workers}")
        print(f"Batch size: {self.batch_size}")
        print(f"Container: {self.container_name}")
        print(f"Index: {self.index_name}")

        # Load or create checkpoint
        checkpoint = None
        if resume and not fresh:
            checkpoint = LocalCheckpoint.load()
            if checkpoint:
                print(f"\nResuming from checkpoint:")
                print(f"  Batch ID: {checkpoint.batch_id}")
                print(f"  Started: {checkpoint.started_at}")
                print(f"  Phase: {checkpoint.phase}")
                print(f"  Completed: {checkpoint.completed_count}/{checkpoint.total_documents}")
                print(f"  Failed: {checkpoint.failed_count}")
                folder = Path(checkpoint.source_folder)

        if checkpoint is None or fresh:
            if folder is None:
                raise ValueError("--folder is required for fresh start")

            folder = Path(folder).resolve()
            if not folder.exists():
                raise ValueError(f"Folder does not exist: {folder}")

            # List PDFs
            pdfs = self.list_local_pdfs(folder)
            if limit:
                pdfs = pdfs[:limit]

            print(f"\nFound {len(pdfs)} PDF files in '{folder.name}'")

            # Create fresh checkpoint
            batch_id = datetime.now().strftime("%Y-%m-%d") + "-" + folder.name[:30]
            checkpoint = LocalCheckpoint(
                batch_id=batch_id,
                source_folder=str(folder),
                started_at=datetime.now().isoformat(),
                last_updated=datetime.now().isoformat(),
                phase="init",
                total_documents=len(pdfs),
                documents={
                    pdf.name: {"filename": pdf.name, "status": "pending", "chunks_created": 0}
                    for pdf in pdfs
                }
            )
            checkpoint.save()

        if dry_run:
            pending = checkpoint.get_pending_documents()
            print(f"\n[DRY RUN] Would process {len(pending)} documents:")
            for doc in pending[:20]:
                print(f"  - {doc}")
            if len(pending) > 20:
                print(f"  ... and {len(pending) - 20} more")
            return checkpoint

        folder = Path(checkpoint.source_folder)

        # Phase 1: Clear blob container
        if not checkpoint.blobs_deleted:
            checkpoint.phase = "blob_cleanup"
            checkpoint.save()
            self.clear_blob_container()
            checkpoint.blobs_deleted = True
            checkpoint.save()
            print("Blob cleanup complete. Checkpoint saved.")

        # Phase 2: Upload new PDFs to blob
        if not checkpoint.blobs_uploaded:
            checkpoint.phase = "blob_upload"
            checkpoint.save()
            self.upload_pdfs_to_blob(folder)
            checkpoint.blobs_uploaded = True
            checkpoint.save()
            print("Blob upload complete. Checkpoint saved.")

        # Phase 3: Clear index
        if not checkpoint.index_cleared:
            checkpoint.phase = "index_clear"
            checkpoint.save()
            self.clear_index()
            checkpoint.index_cleared = True
            checkpoint.save()
            print("Index cleared. Checkpoint saved.")

        # Phase 4: Process documents
        checkpoint.phase = "processing"
        pending = checkpoint.get_pending_documents()
        total_pending = len(pending)

        if total_pending == 0:
            print("\nNo pending documents to process.")
            checkpoint.phase = "completed"
            checkpoint.save()
            return checkpoint

        print(f"\nProcessing {total_pending} documents in batches of {self.batch_size}...")
        print("-" * 60)

        # Get PDF paths for pending documents
        pending_pdfs = [folder / name for name in pending]

        batch_num = 0
        for i in range(0, total_pending, self.batch_size):
            batch = pending_pdfs[i:i + self.batch_size]
            batch_num += 1

            progress = checkpoint.completed_count + checkpoint.failed_count
            total = checkpoint.total_documents
            pct = (progress / total) * 100 if total > 0 else 0

            print(f"\n[Batch {batch_num}] Processing {len(batch)} docs ({progress}/{total} = {pct:.1f}%)")

            self.process_batch(batch, checkpoint)

            # CRITICAL: Save checkpoint after each batch
            checkpoint.save()
            print(f"  >>> CHECKPOINT SAVED: {checkpoint.completed_count}/{total} completed, {checkpoint.total_chunks} chunks")

        # Final summary
        checkpoint.phase = "completed"
        checkpoint.save()

        print(f"\n{'='*60}")
        print("PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"Batch ID: {checkpoint.batch_id}")
        print(f"Total documents: {checkpoint.total_documents}")
        print(f"Completed: {checkpoint.completed_count}")
        print(f"Failed: {checkpoint.failed_count}")
        print(f"Total chunks indexed: {checkpoint.total_chunks}")
        print(f"\nCheckpoint saved to: {CHECKPOINT_FILE}")

        # Show failed documents
        failed = [d for d in checkpoint.documents.values() if d.get("status") == "failed"]
        if failed:
            print(f"\nFailed Documents ({len(failed)}):")
            for doc in failed[:10]:
                print(f"  - {doc['filename']}: {doc.get('error', 'Unknown error')[:60]}")
            if len(failed) > 10:
                print(f"  ... and {len(failed) - 10} more")

        return checkpoint


def show_checkpoint_status():
    """Show current checkpoint status."""
    checkpoint = LocalCheckpoint.load()

    if checkpoint is None:
        print("No checkpoint found.")
        return

    print(f"\nCheckpoint Status")
    print("=" * 50)
    print(f"Batch ID:   {checkpoint.batch_id}")
    print(f"Folder:     {checkpoint.source_folder}")
    print(f"Started:    {checkpoint.started_at}")
    print(f"Updated:    {checkpoint.last_updated}")
    print(f"Phase:      {checkpoint.phase}")
    print(f"Total:      {checkpoint.total_documents}")
    print(f"Completed:  {checkpoint.completed_count}")
    print(f"Failed:     {checkpoint.failed_count}")
    print(f"Pending:    {len(checkpoint.get_pending_documents())}")
    print(f"Chunks:     {checkpoint.total_chunks}")
    print(f"\nBlob cleanup: {'Done' if checkpoint.blobs_deleted else 'Pending'}")
    print(f"Blob upload:  {'Done' if checkpoint.blobs_uploaded else 'Pending'}")
    print(f"Index clear:  {'Done' if checkpoint.index_cleared else 'Pending'}")

    # Show failed documents
    failed = [d for d in checkpoint.documents.values() if d.get("status") == "failed"]
    if failed:
        print(f"\nFailed Documents ({len(failed)}):")
        for doc in failed[:10]:
            print(f"  - {doc['filename']}: {doc.get('error', 'Unknown error')[:60]}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")


def main():
    parser = argparse.ArgumentParser(
        description="Local folder checkpointed ingestion with fresh start support",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fresh", action="store_true", help="Start fresh (delete old blobs, clear index)")
    mode.add_argument("--resume", action="store_true", help="Resume from existing checkpoint")
    mode.add_argument("--status", action="store_true", help="Show checkpoint status")

    parser.add_argument("--folder", type=str, help="Path to folder containing PDFs")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel workers (default: 8)")
    parser.add_argument("--batch-size", type=int, default=100, help="Documents per batch/checkpoint (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing")
    parser.add_argument("--limit", type=int, help="Limit number of documents")
    parser.add_argument("--output", "-o", help="Save results to JSON file")

    args = parser.parse_args()

    if args.status:
        show_checkpoint_status()
        return

    # Default to fresh start if folder provided and no mode specified
    if args.folder and not args.resume:
        args.fresh = True

    if not args.resume and not args.folder:
        parser.error("--folder is required for fresh start (or use --resume to continue)")

    pipeline = LocalFolderPipeline(workers=args.workers, batch_size=args.batch_size)

    folder = Path(args.folder) if args.folder else None
    checkpoint = pipeline.run(
        folder=folder,
        fresh=args.fresh,
        resume=args.resume,
        dry_run=args.dry_run,
        limit=args.limit
    )

    if args.output and not args.dry_run:
        with open(args.output, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
