#!/usr/bin/env python3
"""
Full Pipeline Ingestion Script with Timing Metrics

This script:
1. Clears the Azure Search index (fresh start)
2. Downloads PDFs from Azure Blob Storage
3. Processes each PDF through Docling chunker with timing
4. Indexes chunks to Azure Search with timing
5. Reports detailed timing metrics

Usage:
    python scripts/full_pipeline_ingest.py                    # Full pipeline
    python scripts/full_pipeline_ingest.py --skip-clear       # Don't clear index first
    python scripts/full_pipeline_ingest.py --limit 10         # Process only 10 PDFs
    python scripts/full_pipeline_ingest.py --dry-run          # Preview without processing
"""

import ssl_fix  # Corporate proxy SSL fix - must be first import!

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from dotenv import load_dotenv
load_dotenv()

from azure.storage.blob import BlobServiceClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient


@dataclass
class TimingMetrics:
    """Timing metrics for a single document."""
    filename: str
    download_time_ms: float = 0.0
    docling_time_ms: float = 0.0
    chunking_time_ms: float = 0.0
    indexing_time_ms: float = 0.0
    total_time_ms: float = 0.0
    chunks_created: int = 0
    error: Optional[str] = None


@dataclass
class PipelineResults:
    """Aggregated pipeline results."""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    total_documents: int = 0
    successful_documents: int = 0
    failed_documents: int = 0
    total_chunks: int = 0
    document_metrics: List[TimingMetrics] = field(default_factory=list)

    # Aggregate timing (ms)
    total_download_time: float = 0.0
    total_docling_time: float = 0.0
    total_chunking_time: float = 0.0
    total_indexing_time: float = 0.0
    index_clear_time: float = 0.0

    def add_metric(self, metric: TimingMetrics):
        self.document_metrics.append(metric)
        if metric.error:
            self.failed_documents += 1
        else:
            self.successful_documents += 1
            self.total_download_time += metric.download_time_ms
            self.total_docling_time += metric.docling_time_ms
            self.total_chunking_time += metric.chunking_time_ms
            self.total_indexing_time += metric.indexing_time_ms
            self.total_chunks += metric.chunks_created
        self.total_documents += 1

    def to_dict(self) -> dict:
        total_time = (self.end_time - self.start_time).total_seconds() * 1000 if self.end_time else 0
        return {
            "summary": {
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "total_time_seconds": total_time / 1000,
                "total_documents": self.total_documents,
                "successful_documents": self.successful_documents,
                "failed_documents": self.failed_documents,
                "total_chunks_indexed": self.total_chunks,
            },
            "timing_breakdown_ms": {
                "index_clear": round(self.index_clear_time, 2),
                "total_download": round(self.total_download_time, 2),
                "total_docling": round(self.total_docling_time, 2),
                "total_chunking": round(self.total_chunking_time, 2),
                "total_indexing": round(self.total_indexing_time, 2),
            },
            "averages_per_document_ms": {
                "download": round(self.total_download_time / max(1, self.successful_documents), 2),
                "docling": round(self.total_docling_time / max(1, self.successful_documents), 2),
                "chunking": round(self.total_chunking_time / max(1, self.successful_documents), 2),
                "indexing": round(self.total_indexing_time / max(1, self.successful_documents), 2),
            },
            "documents": [
                {
                    "filename": m.filename,
                    "download_ms": round(m.download_time_ms, 2),
                    "docling_ms": round(m.docling_time_ms, 2),
                    "chunking_ms": round(m.chunking_time_ms, 2),
                    "indexing_ms": round(m.indexing_time_ms, 2),
                    "total_ms": round(m.total_time_ms, 2),
                    "chunks": m.chunks_created,
                    "error": m.error,
                }
                for m in self.document_metrics
            ]
        }


class FullPipelineIngestor:
    """Full pipeline from blob storage to vector database with timing."""

    def __init__(self):
        # Azure Storage
        self.storage_conn_str = os.getenv("STORAGE_CONNECTION_STRING")
        self.container_name = os.getenv("CONTAINER_NAME", "policies-active")

        # Azure Search
        self.search_endpoint = os.getenv("SEARCH_ENDPOINT")
        self.search_api_key = os.getenv("SEARCH_API_KEY")
        self.index_name = "rush-policies"

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

        # Lazy load chunker (heavy import)
        self._chunker = None

    @property
    def chunker(self):
        """Lazy load the chunker to avoid slow imports until needed."""
        if self._chunker is None:
            print("Loading Docling chunker (this may take a moment)...")
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
        """Delete all documents from the search index. Returns time in ms."""
        print(f"\nClearing index '{self.index_name}'...")
        start = time.perf_counter()

        # Get all document IDs
        results = self.search_client.search(
            search_text="*",
            select=["id"],
            top=10000  # Adjust if you have more documents
        )

        doc_ids = [r["id"] for r in results]

        if doc_ids:
            # Delete in batches
            batch_size = 1000
            for i in range(0, len(doc_ids), batch_size):
                batch = doc_ids[i:i + batch_size]
                documents = [{"id": doc_id} for doc_id in batch]
                self.search_client.delete_documents(documents)
                print(f"  Deleted {min(i + batch_size, len(doc_ids))}/{len(doc_ids)} documents")

        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"Cleared {len(doc_ids)} documents in {elapsed_ms:.0f}ms")
        return elapsed_ms

    def download_blob(self, blob_name: str, temp_dir: str) -> tuple[str, float]:
        """Download a blob to temp directory. Returns (local_path, time_ms)."""
        start = time.perf_counter()

        local_path = os.path.join(temp_dir, os.path.basename(blob_name))
        blob_client = self.container_client.get_blob_client(blob_name)

        with open(local_path, "wb") as f:
            download_stream = blob_client.download_blob()
            f.write(download_stream.readall())

        elapsed_ms = (time.perf_counter() - start) * 1000
        return local_path, elapsed_ms

    def process_with_docling(self, pdf_path: str) -> tuple[list, float, float]:
        """
        Process PDF with Docling chunker.
        Returns (chunks, docling_time_ms, chunking_time_ms)
        """
        # The chunker internally uses Docling for PDF parsing
        # We measure the total time and estimate breakdown
        start = time.perf_counter()

        chunks = self.chunker.process_pdf(pdf_path)

        total_ms = (time.perf_counter() - start) * 1000

        # Estimate: ~80% is Docling PDF parsing, ~20% is chunking logic
        docling_ms = total_ms * 0.8
        chunking_ms = total_ms * 0.2

        return chunks, docling_ms, chunking_ms

    def index_chunks(self, chunks: list) -> float:
        """Index chunks to Azure Search. Returns time in ms."""
        if not chunks:
            return 0.0

        start = time.perf_counter()

        # Convert chunks to search documents using built-in method
        documents = [chunk.to_azure_document() for chunk in chunks]

        # Upload in batches
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            self.search_client.upload_documents(batch)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return elapsed_ms

    def process_document(self, blob_name: str, temp_dir: str) -> TimingMetrics:
        """Process a single document through the full pipeline."""
        metric = TimingMetrics(filename=blob_name)
        doc_start = time.perf_counter()

        try:
            # Step 1: Download
            local_path, download_ms = self.download_blob(blob_name, temp_dir)
            metric.download_time_ms = download_ms

            # Step 2 & 3: Docling + Chunking
            chunks, docling_ms, chunking_ms = self.process_with_docling(local_path)
            metric.docling_time_ms = docling_ms
            metric.chunking_time_ms = chunking_ms
            metric.chunks_created = len(chunks)

            # Step 4: Index
            indexing_ms = self.index_chunks(chunks)
            metric.indexing_time_ms = indexing_ms

            # Clean up temp file
            os.remove(local_path)

        except Exception as e:
            metric.error = str(e)

        metric.total_time_ms = (time.perf_counter() - doc_start) * 1000
        return metric

    def run(
        self,
        skip_clear: bool = False,
        limit: Optional[int] = None,
        dry_run: bool = False
    ) -> PipelineResults:
        """Run the full pipeline."""
        results = PipelineResults()

        # List blobs
        print(f"\n{'='*60}")
        print("RUSH Policy Full Pipeline Ingestion")
        print(f"{'='*60}")
        print(f"Container: {self.container_name}")
        print(f"Index: {self.index_name}")

        blobs = self.list_blobs()
        print(f"Found {len(blobs)} PDF files in blob storage")

        if limit:
            blobs = blobs[:limit]
            print(f"Limited to {limit} files")

        if dry_run:
            print("\n[DRY RUN] Would process:")
            for blob in blobs:
                print(f"  - {blob}")
            print(f"\nTotal: {len(blobs)} files")
            return results

        # Clear index
        if not skip_clear:
            results.index_clear_time = self.clear_index()

        # Process each document
        print(f"\nProcessing {len(blobs)} documents...")
        print("-" * 60)

        with tempfile.TemporaryDirectory() as temp_dir:
            for i, blob_name in enumerate(blobs, 1):
                print(f"\n[{i}/{len(blobs)}] {blob_name}")

                metric = self.process_document(blob_name, temp_dir)
                results.add_metric(metric)

                if metric.error:
                    print(f"  ERROR: {metric.error}")
                else:
                    print(f"  Download: {metric.download_time_ms:.0f}ms")
                    print(f"  Docling:  {metric.docling_time_ms:.0f}ms")
                    print(f"  Chunking: {metric.chunking_time_ms:.0f}ms")
                    print(f"  Indexing: {metric.indexing_time_ms:.0f}ms")
                    print(f"  Chunks:   {metric.chunks_created}")
                    print(f"  Total:    {metric.total_time_ms:.0f}ms")

        results.end_time = datetime.now()
        return results


def print_results(results: PipelineResults):
    """Print formatted results."""
    data = results.to_dict()

    print(f"\n{'='*60}")
    print("PIPELINE RESULTS")
    print(f"{'='*60}")

    summary = data["summary"]
    print(f"\nTotal Time: {summary['total_time_seconds']:.1f} seconds")
    print(f"Documents:  {summary['successful_documents']}/{summary['total_documents']} successful")
    print(f"Chunks:     {summary['total_chunks_indexed']} indexed")

    timing = data["timing_breakdown_ms"]
    print(f"\nTiming Breakdown (total milliseconds):")
    print(f"  Index Clear:  {timing['index_clear']:>10.0f}ms")
    print(f"  Downloads:    {timing['total_download']:>10.0f}ms")
    print(f"  Docling:      {timing['total_docling']:>10.0f}ms")
    print(f"  Chunking:     {timing['total_chunking']:>10.0f}ms")
    print(f"  Indexing:     {timing['total_indexing']:>10.0f}ms")

    avg = data["averages_per_document_ms"]
    print(f"\nAverage per Document:")
    print(f"  Download:  {avg['download']:>8.0f}ms")
    print(f"  Docling:   {avg['docling']:>8.0f}ms")
    print(f"  Chunking:  {avg['chunking']:>8.0f}ms")
    print(f"  Indexing:  {avg['indexing']:>8.0f}ms")

    # Failed documents
    failed = [d for d in data["documents"] if d["error"]]
    if failed:
        print(f"\nFailed Documents ({len(failed)}):")
        for doc in failed:
            print(f"  - {doc['filename']}: {doc['error']}")


def main():
    parser = argparse.ArgumentParser(description="Full pipeline ingestion with timing")
    parser.add_argument("--skip-clear", action="store_true", help="Don't clear index first")
    parser.add_argument("--limit", type=int, help="Limit number of documents to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    args = parser.parse_args()

    ingestor = FullPipelineIngestor()
    results = ingestor.run(
        skip_clear=args.skip_clear,
        limit=args.limit,
        dry_run=args.dry_run
    )

    if not args.dry_run:
        print_results(results)

        if args.output:
            with open(args.output, "w") as f:
                json.dump(results.to_dict(), f, indent=2)
            print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
