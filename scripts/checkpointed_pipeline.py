#!/usr/bin/env python3
"""
Checkpointed Pipeline Ingestion - Resumable Processing with Progress Tracking

This script provides:
- Checkpoint-based resume capability (survives crashes/restarts)
- Configurable parallel workers (default: 4 for 40% of 11 cores)
- Progress saved after each batch
- Detailed timing metrics
- JSON checkpoint file for tracking state

Usage:
    # Fresh start (clears checkpoint)
    python scripts/checkpointed_pipeline.py --fresh

    # Resume from last checkpoint
    python scripts/checkpointed_pipeline.py --resume

    # Specify workers (40% of resources)
    python scripts/checkpointed_pipeline.py --workers 4 --batch-size 25

    # Dry run to see what would be processed
    python scripts/checkpointed_pipeline.py --dry-run
"""

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Set

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

from azure.storage.blob import BlobServiceClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient


# Checkpoint file location
CHECKPOINT_FILE = Path(__file__).parent.parent / "data" / "pipeline_checkpoint.json"


@dataclass
class DocumentCheckpoint:
    """Checkpoint for a single document."""
    filename: str
    status: str  # "pending", "processing", "completed", "failed"
    chunks_created: int = 0
    processing_time_ms: float = 0.0
    error: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class PipelineCheckpoint:
    """Full pipeline checkpoint state."""
    started_at: str
    last_updated: str
    phase: str  # "init", "clearing", "processing", "completed"
    total_documents: int = 0
    completed_count: int = 0
    failed_count: int = 0
    total_chunks: int = 0
    index_cleared: bool = False
    documents: Dict[str, dict] = field(default_factory=dict)

    # Timing aggregates
    total_download_ms: float = 0.0
    total_processing_ms: float = 0.0
    total_indexing_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineCheckpoint":
        return cls(**data)

    def save(self, path: Path = CHECKPOINT_FILE):
        """Save checkpoint to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.last_updated = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path = CHECKPOINT_FILE) -> Optional["PipelineCheckpoint"]:
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


class CheckpointedPipeline:
    """Pipeline with checkpoint/resume support."""

    def __init__(self, workers: int = 4, batch_size: int = 25):
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

    @property
    def chunker(self):
        """Lazy load the chunker."""
        if self._chunker is None:
            print("Loading Docling chunker...")
            from preprocessing.chunker import PolicyChunker
            self._chunker = PolicyChunker()
            print("Chunker loaded.")
        return self._chunker

    def list_blobs(self) -> List[str]:
        """List all PDF blobs in the container."""
        blobs = []
        for blob in self.container_client.list_blobs():
            if blob.name.lower().endswith('.pdf'):
                blobs.append(blob.name)
        return sorted(blobs)

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

    def process_single_document(self, blob_name: str, temp_dir: str) -> Dict:
        """Process a single document. Returns result dict."""
        result = {
            "filename": blob_name,
            "status": "completed",
            "chunks_created": 0,
            "processing_time_ms": 0.0,
            "error": None
        }
        start = time.perf_counter()

        try:
            # Download
            local_path = os.path.join(temp_dir, os.path.basename(blob_name))
            blob_client = self.container_client.get_blob_client(blob_name)
            with open(local_path, "wb") as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())

            # Process with Docling
            chunks = self.chunker.process_pdf(local_path)
            result["chunks_created"] = len(chunks)

            # Index chunks
            if chunks:
                documents = [chunk.to_azure_document() for chunk in chunks]
                batch_size = 100
                for i in range(0, len(documents), batch_size):
                    batch = documents[i:i + batch_size]
                    self.search_client.upload_documents(batch)

            # Cleanup
            os.remove(local_path)

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        result["processing_time_ms"] = (time.perf_counter() - start) * 1000
        return result

    def process_batch(self, blob_names: List[str], checkpoint: PipelineCheckpoint) -> int:
        """Process a batch of documents with parallel workers."""
        processed = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(self.process_single_document, blob, temp_dir): blob
                    for blob in blob_names
                }

                for future in as_completed(futures):
                    blob_name = futures[future]
                    try:
                        result = future.result()

                        if result["status"] == "completed":
                            checkpoint.mark_completed(
                                blob_name,
                                result["chunks_created"],
                                result["processing_time_ms"]
                            )
                            print(f"  [OK] {blob_name} ({result['chunks_created']} chunks, {result['processing_time_ms']:.0f}ms)")
                        else:
                            checkpoint.mark_failed(blob_name, result["error"])
                            print(f"  [FAIL] {blob_name}: {result['error']}")

                    except Exception as e:
                        checkpoint.mark_failed(blob_name, str(e))
                        print(f"  [FAIL] {blob_name}: {e}")

                    processed += 1

        return processed

    def run(
        self,
        fresh: bool = False,
        resume: bool = False,
        skip_clear: bool = False,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> PipelineCheckpoint:
        """Run the pipeline with checkpoint support."""

        print(f"\n{'='*60}")
        print("CHECKPOINTED PIPELINE INGESTION")
        print(f"{'='*60}")
        print(f"Workers: {self.workers}")
        print(f"Batch size: {self.batch_size}")
        print(f"Container: {self.container_name}")
        print(f"Index: {self.index_name}")

        # Load or create checkpoint
        checkpoint = None
        if resume and not fresh:
            checkpoint = PipelineCheckpoint.load()
            if checkpoint:
                print(f"\nResuming from checkpoint:")
                print(f"  Started: {checkpoint.started_at}")
                print(f"  Completed: {checkpoint.completed_count}/{checkpoint.total_documents}")
                print(f"  Failed: {checkpoint.failed_count}")

        if checkpoint is None or fresh:
            # Fresh start
            blobs = self.list_blobs()
            if limit:
                blobs = blobs[:limit]

            print(f"\nFound {len(blobs)} PDF files")

            checkpoint = PipelineCheckpoint(
                started_at=datetime.now().isoformat(),
                last_updated=datetime.now().isoformat(),
                phase="init",
                total_documents=len(blobs),
                documents={
                    blob: {"filename": blob, "status": "pending", "chunks_created": 0}
                    for blob in blobs
                }
            )

        if dry_run:
            pending = checkpoint.get_pending_documents()
            print(f"\n[DRY RUN] Would process {len(pending)} documents:")
            for blob in pending[:20]:
                print(f"  - {blob}")
            if len(pending) > 20:
                print(f"  ... and {len(pending) - 20} more")
            return checkpoint

        # Phase 1: Clear index (if needed)
        if not checkpoint.index_cleared and not skip_clear:
            checkpoint.phase = "clearing"
            checkpoint.save()
            self.clear_index()
            checkpoint.index_cleared = True
            checkpoint.save()
            print("Index cleared. Checkpoint saved.")

        # Phase 2: Process documents
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

        batch_num = 0
        for i in range(0, total_pending, self.batch_size):
            batch = pending[i:i + self.batch_size]
            batch_num += 1

            progress = checkpoint.completed_count + checkpoint.failed_count
            total = checkpoint.total_documents
            pct = (progress / total) * 100 if total > 0 else 0

            print(f"\n[Batch {batch_num}] Processing {len(batch)} docs ({progress}/{total} = {pct:.1f}%)")

            self.process_batch(batch, checkpoint)

            # Save checkpoint after each batch
            checkpoint.save()
            print(f"  Checkpoint saved. Progress: {checkpoint.completed_count}/{total}")

        # Final summary
        checkpoint.phase = "completed"
        checkpoint.save()

        print(f"\n{'='*60}")
        print("PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"Total documents: {checkpoint.total_documents}")
        print(f"Completed: {checkpoint.completed_count}")
        print(f"Failed: {checkpoint.failed_count}")
        print(f"Total chunks indexed: {checkpoint.total_chunks}")
        print(f"\nCheckpoint saved to: {CHECKPOINT_FILE}")

        return checkpoint


def show_checkpoint_status():
    """Show current checkpoint status."""
    checkpoint = PipelineCheckpoint.load()

    if checkpoint is None:
        print("No checkpoint found.")
        return

    print(f"\nCheckpoint Status")
    print("=" * 50)
    print(f"Started:    {checkpoint.started_at}")
    print(f"Updated:    {checkpoint.last_updated}")
    print(f"Phase:      {checkpoint.phase}")
    print(f"Total:      {checkpoint.total_documents}")
    print(f"Completed:  {checkpoint.completed_count}")
    print(f"Failed:     {checkpoint.failed_count}")
    print(f"Pending:    {len(checkpoint.get_pending_documents())}")
    print(f"Chunks:     {checkpoint.total_chunks}")

    # Show failed documents
    failed = [d for d in checkpoint.documents.values() if d.get("status") == "failed"]
    if failed:
        print(f"\nFailed Documents ({len(failed)}):")
        for doc in failed[:10]:
            print(f"  - {doc['filename']}: {doc.get('error', 'Unknown error')}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")


def main():
    parser = argparse.ArgumentParser(
        description="Checkpointed pipeline ingestion with resume support",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fresh", action="store_true", help="Start fresh (ignore existing checkpoint)")
    mode.add_argument("--resume", action="store_true", help="Resume from existing checkpoint")
    mode.add_argument("--status", action="store_true", help="Show checkpoint status")

    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers (default: 4)")
    parser.add_argument("--batch-size", type=int, default=25, help="Documents per batch (default: 25)")
    parser.add_argument("--skip-clear", action="store_true", help="Don't clear index first")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing")
    parser.add_argument("--limit", type=int, help="Limit number of documents")
    parser.add_argument("--output", "-o", help="Save results to JSON file")

    args = parser.parse_args()

    if args.status:
        show_checkpoint_status()
        return

    pipeline = CheckpointedPipeline(workers=args.workers, batch_size=args.batch_size)

    checkpoint = pipeline.run(
        fresh=args.fresh,
        resume=args.resume,
        skip_clear=args.skip_clear,
        dry_run=args.dry_run,
        limit=args.limit
    )

    if args.output and not args.dry_run:
        with open(args.output, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
