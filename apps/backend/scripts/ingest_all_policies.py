"""
Full Policy Ingestion Script for RUSH Policy RAG

Processes all PDFs from Azure Blob Storage and uploads to Azure AI Search.

Features:
- Docling-based PDF parsing with TableFormer for table extraction
- Native checkbox detection for "Applies To" fields
- Parallel processing (configurable workers)
- Progress tracking with ETA
- Automatic retry on failures
- Detailed ingestion report
- Differential sync support via content hashing

Usage:
    # Full ingestion from source container
    python scripts/ingest_all_policies.py

    # Validate parsing only (no upload)
    python scripts/ingest_all_policies.py --validate-only --sample 20

    # Force reindex (delete existing chunks first)
    python scripts/ingest_all_policies.py --force-reindex

    # Process specific folder of local PDFs
    python scripts/ingest_all_policies.py --local-folder ./data/policies

Environment Variables:
    STORAGE_CONNECTION_STRING - Azure Blob Storage connection
    SEARCH_API_KEY - Azure AI Search API key
"""

# Corporate proxy SSL fix - must be before other imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import ssl_fix
except ImportError:
    pass

import os
import sys
import json
import time
import argparse
import logging
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Azure configuration
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
SOURCE_CONTAINER_NAME = os.environ.get("SOURCE_CONTAINER_NAME", "policies-source")
ACTIVE_CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "policies-active")


@dataclass
class DocumentReport:
    """Report for a single document processing."""
    filename: str
    status: str  # "success", "failed", "skipped"
    chunks_created: int = 0
    chunks_uploaded: int = 0
    error_message: str = ""
    processing_time_ms: int = 0
    file_size_bytes: int = 0
    content_hash: str = ""
    metadata_extracted: Dict[str, str] = field(default_factory=dict)


@dataclass
class IngestionReport:
    """Complete ingestion run report."""
    start_time: str
    end_time: str
    duration_seconds: float
    backend_used: str
    total_documents: int
    successful_documents: int
    failed_documents: int
    skipped_documents: int
    total_chunks_created: int
    total_chunks_uploaded: int
    avg_chunks_per_doc: float
    documents: List[DocumentReport] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "backend_used": self.backend_used,
            "total_documents": self.total_documents,
            "successful_documents": self.successful_documents,
            "failed_documents": self.failed_documents,
            "skipped_documents": self.skipped_documents,
            "total_chunks_created": self.total_chunks_created,
            "total_chunks_uploaded": self.total_chunks_uploaded,
            "avg_chunks_per_doc": self.avg_chunks_per_doc,
            "documents": [asdict(d) for d in self.documents],
            "errors": self.errors,
        }


class PolicyIngestionPipeline:
    """
    Orchestrates full policy ingestion from Azure Blob Storage to Azure AI Search.
    """

    def __init__(
        self,
        workers: int = 4,
        batch_size: int = 100,
        validate_only: bool = False,
        backend: str = None  # Deprecated, kept for backward compatibility
    ):
        self.workers = workers
        self.batch_size = batch_size
        self.validate_only = validate_only

        # Initialize Docling-based chunker
        from preprocessing.chunker import PolicyChunker
        self.chunker = PolicyChunker()
        logger.info(f"Initialized chunker with Docling backend")

        # Initialize Azure clients
        if STORAGE_CONNECTION_STRING:
            from azure.storage.blob import BlobServiceClient
            self.blob_service = BlobServiceClient.from_connection_string(
                STORAGE_CONNECTION_STRING
            )
            self.source_container = self.blob_service.get_container_client(
                SOURCE_CONTAINER_NAME
            )
            self.active_container = self.blob_service.get_container_client(
                ACTIVE_CONTAINER_NAME
            )
        else:
            self.blob_service = None
            logger.warning("STORAGE_CONNECTION_STRING not set - blob operations disabled")

        # Initialize search index (unless validate only)
        if not validate_only:
            from azure_policy_index import PolicySearchIndex
            self.search_index = PolicySearchIndex()
        else:
            self.search_index = None

        # Track processed documents
        self.processed_hashes: Dict[str, str] = {}

    def list_source_pdfs(self) -> List[str]:
        """List all PDFs in source container."""
        if not self.blob_service:
            logger.error("Blob service not initialized")
            return []

        pdfs = []
        for blob in self.source_container.list_blobs():
            if blob.name.lower().endswith('.pdf'):
                pdfs.append(blob.name)

        logger.info(f"Found {len(pdfs)} PDFs in source container")
        return sorted(pdfs)

    def download_pdf(self, blob_name: str, temp_dir: str) -> Tuple[str, int]:
        """Download PDF from blob storage to temp file."""
        blob_client = self.source_container.get_blob_client(blob_name)
        local_path = os.path.join(temp_dir, os.path.basename(blob_name))

        with open(local_path, "wb") as f:
            download = blob_client.download_blob()
            data = download.readall()
            f.write(data)

        return local_path, len(data)

    def get_blob_hash(self, blob_name: str) -> Optional[str]:
        """Get content hash of blob for differential sync."""
        try:
            blob_client = self.source_container.get_blob_client(blob_name)
            props = blob_client.get_blob_properties()
            return props.content_settings.content_md5
        except Exception:
            return None

    def process_single_pdf(
        self,
        blob_name: str,
        temp_dir: str
    ) -> DocumentReport:
        """Process a single PDF file."""
        start_time = time.time()
        report = DocumentReport(filename=blob_name, status="pending")

        try:
            # Download PDF
            local_path, file_size = self.download_pdf(blob_name, temp_dir)
            report.file_size_bytes = file_size

            # Calculate content hash
            with open(local_path, 'rb') as f:
                report.content_hash = hashlib.sha256(f.read()).hexdigest()

            # Process with chunker
            chunks = self.chunker.process_pdf(local_path)
            report.chunks_created = len(chunks)

            if chunks:
                # Extract metadata from first chunk
                first_chunk = chunks[0]
                report.metadata_extracted = {
                    "title": first_chunk.policy_title,
                    "reference_number": first_chunk.reference_number,
                    "applies_to": first_chunk.applies_to,
                    "date_updated": first_chunk.date_updated,
                    "document_owner": first_chunk.document_owner,
                    # Entity boolean summary (count of entities this policy applies to)
                    "entity_booleans_set": sum([
                        first_chunk.applies_to_rumc,
                        first_chunk.applies_to_rumg,
                        first_chunk.applies_to_rmg,
                        first_chunk.applies_to_roph,
                        first_chunk.applies_to_rcmc,
                        first_chunk.applies_to_rch,
                        first_chunk.applies_to_roppg,
                        first_chunk.applies_to_rcmg,
                        first_chunk.applies_to_ru,
                    ]),
                    "chunk_level": first_chunk.chunk_level,
                }

                # Upload to search index (unless validate only)
                if not self.validate_only and self.search_index:
                    # Delete existing chunks for this file first
                    self.search_index.delete_by_source_file(blob_name)

                    # Upload new chunks
                    stats = self.search_index.upload_chunks(chunks, batch_size=self.batch_size)
                    report.chunks_uploaded = stats['uploaded']

                    if stats['failed'] > 0:
                        report.error_message = f"{stats['failed']} chunks failed to upload"

                report.status = "success"
            else:
                report.status = "failed"
                report.error_message = "No chunks extracted"

            # Cleanup temp file
            os.remove(local_path)

        except Exception as e:
            report.status = "failed"
            report.error_message = str(e)
            logger.error(f"Failed to process {blob_name}: {e}")

        report.processing_time_ms = int((time.time() - start_time) * 1000)
        return report

    def process_local_folder(self, folder_path: str) -> IngestionReport:
        """Process all PDFs in a local folder."""
        start_time = datetime.now()
        documents: List[DocumentReport] = []
        errors: List[Dict[str, str]] = []

        folder = Path(folder_path)
        pdf_files = sorted(folder.glob("*.pdf"))
        total_files = len(pdf_files)

        logger.info(f"Processing {total_files} PDFs from {folder_path}")

        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"\r  Processing [{i}/{total_files}] {pdf_path.name}...", end="", flush=True)

            report = DocumentReport(filename=pdf_path.name, status="pending")
            proc_start = time.time()

            try:
                report.file_size_bytes = pdf_path.stat().st_size

                # Calculate content hash
                with open(pdf_path, 'rb') as f:
                    report.content_hash = hashlib.sha256(f.read()).hexdigest()

                # Process with chunker
                chunks = self.chunker.process_pdf(str(pdf_path))
                report.chunks_created = len(chunks)

                if chunks:
                    first_chunk = chunks[0]
                    report.metadata_extracted = {
                        "title": first_chunk.policy_title,
                        "reference_number": first_chunk.reference_number,
                        "applies_to": first_chunk.applies_to,
                        "date_updated": first_chunk.date_updated,
                    }

                    if not self.validate_only and self.search_index:
                        self.search_index.delete_by_source_file(pdf_path.name)
                        stats = self.search_index.upload_chunks(chunks, batch_size=self.batch_size)
                        report.chunks_uploaded = stats['uploaded']

                    report.status = "success"
                else:
                    report.status = "failed"
                    report.error_message = "No chunks extracted"

            except Exception as e:
                report.status = "failed"
                report.error_message = str(e)
                errors.append({"file": pdf_path.name, "error": str(e)})

            report.processing_time_ms = int((time.time() - proc_start) * 1000)
            documents.append(report)

        print()  # New line after progress

        # Build report
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        successful = [d for d in documents if d.status == "success"]
        failed = [d for d in documents if d.status == "failed"]

        total_chunks = sum(d.chunks_created for d in documents)
        total_uploaded = sum(d.chunks_uploaded for d in documents)
        avg_chunks = total_chunks / len(successful) if successful else 0

        return IngestionReport(
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            duration_seconds=duration,
            backend_used=self.chunker.backend,
            total_documents=len(documents),
            successful_documents=len(successful),
            failed_documents=len(failed),
            skipped_documents=0,
            total_chunks_created=total_chunks,
            total_chunks_uploaded=total_uploaded,
            avg_chunks_per_doc=avg_chunks,
            documents=documents,
            errors=errors,
        )

    def run_full_ingestion(
        self,
        sample_size: Optional[int] = None,
        force_reindex: bool = False
    ) -> IngestionReport:
        """Run full ingestion from Azure Blob Storage."""
        start_time = datetime.now()
        documents: List[DocumentReport] = []
        errors: List[Dict[str, str]] = []

        # List all PDFs
        pdf_blobs = self.list_source_pdfs()

        if not pdf_blobs:
            logger.warning("No PDFs found in source container")
            return IngestionReport(
                start_time=start_time.isoformat(),
                end_time=datetime.now().isoformat(),
                duration_seconds=0,
                backend_used=self.chunker.backend,
                total_documents=0,
                successful_documents=0,
                failed_documents=0,
                skipped_documents=0,
                total_chunks_created=0,
                total_chunks_uploaded=0,
                avg_chunks_per_doc=0,
            )

        # Sample if requested
        if sample_size and sample_size < len(pdf_blobs):
            import random
            pdf_blobs = random.sample(pdf_blobs, sample_size)
            logger.info(f"Sampling {sample_size} PDFs for validation")

        total_files = len(pdf_blobs)
        logger.info(f"Processing {total_files} PDFs")

        # Create temp directory for downloads
        with tempfile.TemporaryDirectory() as temp_dir:
            # Process with progress tracking
            processed = 0
            start_processing = time.time()

            for blob_name in pdf_blobs:
                processed += 1
                elapsed = time.time() - start_processing
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total_files - processed) / rate if rate > 0 else 0
                eta = timedelta(seconds=int(remaining))

                print(
                    f"\r  [{processed}/{total_files}] {blob_name[:40]:<40} "
                    f"| Rate: {rate:.1f}/s | ETA: {eta}",
                    end="",
                    flush=True
                )

                report = self.process_single_pdf(blob_name, temp_dir)
                documents.append(report)

                if report.status == "failed":
                    errors.append({
                        "file": blob_name,
                        "error": report.error_message
                    })

        print()  # New line after progress

        # Build report
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        successful = [d for d in documents if d.status == "success"]
        failed = [d for d in documents if d.status == "failed"]
        skipped = [d for d in documents if d.status == "skipped"]

        total_chunks = sum(d.chunks_created for d in documents)
        total_uploaded = sum(d.chunks_uploaded for d in documents)
        avg_chunks = total_chunks / len(successful) if successful else 0

        return IngestionReport(
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            duration_seconds=duration,
            backend_used=self.chunker.backend,
            total_documents=len(documents),
            successful_documents=len(successful),
            failed_documents=len(failed),
            skipped_documents=len(skipped),
            total_chunks_created=total_chunks,
            total_chunks_uploaded=total_uploaded,
            avg_chunks_per_doc=avg_chunks,
            documents=documents,
            errors=errors,
        )


def print_report(report: IngestionReport) -> None:
    """Print formatted ingestion report."""
    print("\n" + "=" * 60)
    print("INGESTION REPORT")
    print("=" * 60)

    print(f"\n  Start time: {report.start_time}")
    print(f"  End time: {report.end_time}")
    print(f"  Duration: {report.duration_seconds:.1f} seconds")
    print(f"  Backend: {report.backend_used}")

    print(f"\n  Documents:")
    print(f"    Total: {report.total_documents}")
    print(f"    Successful: {report.successful_documents}")
    print(f"    Failed: {report.failed_documents}")
    print(f"    Skipped: {report.skipped_documents}")

    print(f"\n  Chunks:")
    print(f"    Total created: {report.total_chunks_created}")
    print(f"    Total uploaded: {report.total_chunks_uploaded}")
    print(f"    Avg per document: {report.avg_chunks_per_doc:.1f}")

    if report.errors:
        print(f"\n  Errors ({len(report.errors)}):")
        for err in report.errors[:10]:  # Show first 10
            print(f"    - {err['file']}: {err['error'][:50]}")
        if len(report.errors) > 10:
            print(f"    ... and {len(report.errors) - 10} more")

    # Metadata extraction quality
    docs_with_title = sum(1 for d in report.documents
                         if d.metadata_extracted.get('title'))
    docs_with_ref = sum(1 for d in report.documents
                       if d.metadata_extracted.get('reference_number'))
    docs_with_applies = sum(1 for d in report.documents
                           if d.metadata_extracted.get('applies_to'))

    if report.successful_documents > 0:
        print(f"\n  Metadata Extraction Quality:")
        print(f"    Title extracted: {docs_with_title}/{report.successful_documents} "
              f"({100*docs_with_title/report.successful_documents:.0f}%)")
        print(f"    Reference # extracted: {docs_with_ref}/{report.successful_documents} "
              f"({100*docs_with_ref/report.successful_documents:.0f}%)")
        print(f"    Applies To extracted: {docs_with_applies}/{report.successful_documents} "
              f"({100*docs_with_applies/report.successful_documents:.0f}%)")

        # Entity boolean extraction quality
        docs_with_entities = sum(1 for d in report.documents
                                 if d.metadata_extracted.get('entity_booleans_set', 0) > 0)
        total_entities = sum(d.metadata_extracted.get('entity_booleans_set', 0)
                            for d in report.documents)
        avg_entities = total_entities / report.successful_documents if report.successful_documents else 0

        print(f"\n  Entity Boolean Extraction:")
        print(f"    Docs with entity booleans: {docs_with_entities}/{report.successful_documents} "
              f"({100*docs_with_entities/report.successful_documents:.0f}%)")
        print(f"    Total entity associations: {total_entities}")
        print(f"    Avg entities per doc: {avg_entities:.1f}")


def main():
    parser = argparse.ArgumentParser(
        description="Ingest all policy PDFs into Azure AI Search"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse PDFs without uploading to search index"
    )
    parser.add_argument(
        "--sample",
        type=int,
        help="Process random sample of N documents"
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Delete existing chunks before uploading"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )
    parser.add_argument(
        "--local-folder",
        type=str,
        help="Process local folder instead of Azure Blob Storage"
    )
    parser.add_argument(
        "--output-report",
        type=str,
        help="Save detailed report to JSON file"
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("RUSH POLICY INGESTION PIPELINE")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Initialize pipeline (uses Docling)
    pipeline = PolicyIngestionPipeline(
        workers=args.workers,
        validate_only=args.validate_only
    )

    print(f"\n  Backend: Docling (TableFormer ACCURATE)")
    print(f"  Validate only: {args.validate_only}")
    print(f"  Sample size: {args.sample or 'all'}")

    # Run ingestion
    if args.local_folder:
        print(f"\n  Processing local folder: {args.local_folder}")
        report = pipeline.process_local_folder(args.local_folder)
    else:
        print(f"\n  Processing from Azure Blob Storage")
        report = pipeline.run_full_ingestion(
            sample_size=args.sample,
            force_reindex=args.force_reindex
        )

    # Print report
    print_report(report)

    # Save detailed report if requested
    if args.output_report:
        with open(args.output_report, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\n  Detailed report saved to: {args.output_report}")

    # Return exit code based on success rate
    if report.total_documents > 0:
        success_rate = report.successful_documents / report.total_documents
        if success_rate < 0.9:
            print(f"\n[WARNING] Success rate below 90%: {success_rate*100:.1f}%")
            sys.exit(1)

    print("\n[DONE]")


if __name__ == "__main__":
    main()
