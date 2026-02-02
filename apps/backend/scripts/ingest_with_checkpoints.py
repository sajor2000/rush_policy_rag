#!/usr/bin/env python3
"""
Checkpoint-Enabled Policy Ingestion Script for RUSH Policy RAG

Processes PDFs from a local folder with checkpoint/resume support.
Saves progress every N files to allow resumption after interruption.

Features:
- Checkpoint/resume support (saves every 50 files by default)
- Progress tracking with detailed metrics
- Detailed final report
- Skips non-PDF files automatically

Usage:
    # Process all PDFs from SimpleExport folder with checkpoints
    python scripts/ingest_with_checkpoints.py \
        --local-folder "../../../SimpleExport_27236_2026_01_22" \
        --checkpoint-file /tmp/checkpoint.json

    # Resume from previous checkpoint
    python scripts/ingest_with_checkpoints.py \
        --local-folder "../../../SimpleExport_27236_2026_01_22" \
        --checkpoint-file /tmp/checkpoint.json \
        --resume

    # Custom batch size for checkpoints
    python scripts/ingest_with_checkpoints.py \
        --local-folder "../../../SimpleExport_27236_2026_01_22" \
        --checkpoint-file /tmp/checkpoint.json \
        --batch-size 100
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
import json
import time
import argparse
import logging
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field, asdict

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


@dataclass
class Checkpoint:
    """Checkpoint data for resume support."""
    total_files: int = 0
    processed_files: List[str] = field(default_factory=list)
    failed_files: List[Dict[str, str]] = field(default_factory=list)
    last_index: int = 0
    last_save_time: str = ""
    chunks_created: int = 0
    chunks_uploaded: int = 0
    start_time: str = ""

    def save(self, path: str):
        """Save checkpoint to file."""
        self.last_save_time = datetime.now().isoformat()
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
        logger.info(f"Checkpoint saved: {len(self.processed_files)}/{self.total_files} files processed")

    @classmethod
    def load(cls, path: str) -> 'Checkpoint':
        """Load checkpoint from file."""
        if not os.path.exists(path):
            return cls()
        with open(path, 'r') as f:
            data = json.load(f)
        return cls(**data)


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
    resumed_from_checkpoint: bool = False

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
            "resumed_from_checkpoint": self.resumed_from_checkpoint,
        }


class CheckpointIngestionPipeline:
    """
    Policy ingestion pipeline with checkpoint/resume support.
    """

    def __init__(
        self,
        checkpoint_file: str,
        batch_size: int = 50,
        validate_only: bool = False,
    ):
        self.checkpoint_file = checkpoint_file
        self.batch_size = batch_size
        self.validate_only = validate_only
        self.checkpoint = Checkpoint()

        # Initialize Docling-based chunker
        from preprocessing.chunker import PolicyChunker
        self.chunker = PolicyChunker()
        logger.info(f"Initialized chunker with Docling backend")

        # Initialize search index (unless validate only)
        if not validate_only:
            from azure_policy_index import PolicySearchIndex
            self.search_index = PolicySearchIndex()
        else:
            self.search_index = None

    def process_single_pdf(self, pdf_path: Path) -> DocumentReport:
        """Process a single PDF file."""
        start_time = time.time()
        report = DocumentReport(filename=pdf_path.name, status="pending")

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
                    "title": first_chunk.policy_title or "",
                    "reference_number": first_chunk.reference_number or "",
                    "applies_to": first_chunk.applies_to or "",
                    "date_updated": first_chunk.date_updated or "",
                    "document_owner": getattr(first_chunk, 'document_owner', "") or "",
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
                }

                if not self.validate_only and self.search_index:
                    # Delete existing chunks for this file first
                    self.search_index.delete_by_source_file(pdf_path.name)

                    # Upload new chunks
                    stats = self.search_index.upload_chunks(chunks, batch_size=100)
                    report.chunks_uploaded = stats['uploaded']

                    if stats['failed'] > 0:
                        report.error_message = f"{stats['failed']} chunks failed to upload"

                report.status = "success"
            else:
                report.status = "failed"
                report.error_message = "No chunks extracted"

        except Exception as e:
            report.status = "failed"
            report.error_message = str(e)
            logger.error(f"Failed to process {pdf_path.name}: {e}")

        report.processing_time_ms = int((time.time() - start_time) * 1000)
        return report

    def run_ingestion(
        self,
        folder_path: str,
        resume: bool = False,
    ) -> IngestionReport:
        """Run ingestion with checkpoint support."""
        folder = Path(folder_path)

        # List all PDFs (sorted for consistent ordering)
        all_pdfs = sorted([f for f in folder.glob("*.pdf") if f.is_file()])
        total_files = len(all_pdfs)

        logger.info(f"Found {total_files} PDF files in {folder_path}")

        # Load or initialize checkpoint
        if resume and os.path.exists(self.checkpoint_file):
            self.checkpoint = Checkpoint.load(self.checkpoint_file)
            logger.info(f"Resuming from checkpoint: {len(self.checkpoint.processed_files)} files already processed")
            already_processed: Set[str] = set(self.checkpoint.processed_files)
            already_failed: Set[str] = set(f['file'] for f in self.checkpoint.failed_files)
            resumed = True
        else:
            self.checkpoint = Checkpoint()
            self.checkpoint.total_files = total_files
            self.checkpoint.start_time = datetime.now().isoformat()
            already_processed = set()
            already_failed = set()
            resumed = False

        # Filter to only unprocessed files
        pdfs_to_process = [
            p for p in all_pdfs
            if p.name not in already_processed and p.name not in already_failed
        ]

        logger.info(f"Processing {len(pdfs_to_process)} files (skipping {len(already_processed) + len(already_failed)} already handled)")

        # Track for this session
        documents: List[DocumentReport] = []
        errors: List[Dict[str, str]] = []
        session_start = datetime.now()
        start_processing = time.time()

        for i, pdf_path in enumerate(pdfs_to_process, 1):
            # Progress display
            total_done = len(already_processed) + len(already_failed) + i
            elapsed = time.time() - start_processing
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (len(pdfs_to_process) - i) / rate if rate > 0 else 0
            eta = timedelta(seconds=int(remaining))

            # Truncate filename for display
            display_name = pdf_path.name[:45] + "..." if len(pdf_path.name) > 48 else pdf_path.name
            print(
                f"\r  [{total_done}/{total_files}] {display_name:<50} "
                f"| Rate: {rate:.1f}/s | ETA: {eta}    ",
                end="",
                flush=True
            )

            # Process the PDF
            report = self.process_single_pdf(pdf_path)
            documents.append(report)

            if report.status == "success":
                self.checkpoint.processed_files.append(pdf_path.name)
                self.checkpoint.chunks_created += report.chunks_created
                self.checkpoint.chunks_uploaded += report.chunks_uploaded
            else:
                self.checkpoint.failed_files.append({
                    "file": pdf_path.name,
                    "error": report.error_message
                })
                errors.append({
                    "file": pdf_path.name,
                    "error": report.error_message
                })

            self.checkpoint.last_index = total_done

            # Save checkpoint every batch_size files
            if i % self.batch_size == 0:
                self.checkpoint.save(self.checkpoint_file)

        print()  # New line after progress

        # Save final checkpoint
        self.checkpoint.save(self.checkpoint_file)

        # Build final report
        end_time = datetime.now()
        duration = (end_time - session_start).total_seconds()

        successful = [d for d in documents if d.status == "success"]
        failed = [d for d in documents if d.status == "failed"]

        # Include prior session stats if resumed
        total_chunks_created = self.checkpoint.chunks_created
        total_chunks_uploaded = self.checkpoint.chunks_uploaded
        total_successful = len(self.checkpoint.processed_files)
        total_failed = len(self.checkpoint.failed_files)

        avg_chunks = total_chunks_created / total_successful if total_successful else 0

        return IngestionReport(
            start_time=self.checkpoint.start_time,
            end_time=end_time.isoformat(),
            duration_seconds=duration,
            backend_used=self.chunker.backend,
            total_documents=total_files,
            successful_documents=total_successful,
            failed_documents=total_failed,
            skipped_documents=0,
            total_chunks_created=total_chunks_created,
            total_chunks_uploaded=total_chunks_uploaded,
            avg_chunks_per_doc=avg_chunks,
            documents=documents,
            errors=errors,
            resumed_from_checkpoint=resumed,
        )


def print_report(report: IngestionReport) -> None:
    """Print formatted ingestion report."""
    print("\n" + "=" * 60)
    print("INGESTION REPORT")
    print("=" * 60)

    if report.resumed_from_checkpoint:
        print("\n  [RESUMED FROM CHECKPOINT]")

    print(f"\n  Start time: {report.start_time}")
    print(f"  End time: {report.end_time}")
    print(f"  Session duration: {report.duration_seconds:.1f} seconds")
    print(f"  Backend: {report.backend_used}")

    print(f"\n  Documents (TOTAL across all sessions):")
    print(f"    Total PDFs: {report.total_documents}")
    print(f"    Successful: {report.successful_documents}")
    print(f"    Failed: {report.failed_documents}")

    success_rate = (report.successful_documents / report.total_documents * 100) if report.total_documents else 0
    print(f"    Success rate: {success_rate:.1f}%")

    print(f"\n  Chunks (TOTAL across all sessions):")
    print(f"    Total created: {report.total_chunks_created}")
    print(f"    Total uploaded: {report.total_chunks_uploaded}")
    print(f"    Avg per document: {report.avg_chunks_per_doc:.1f}")

    if report.errors:
        print(f"\n  Errors in this session ({len(report.errors)}):")
        for err in report.errors[:10]:  # Show first 10
            print(f"    - {err['file'][:40]}: {err['error'][:50]}")
        if len(report.errors) > 10:
            print(f"    ... and {len(report.errors) - 10} more")

    # Metadata extraction quality (this session only)
    if report.documents:
        docs_with_title = sum(1 for d in report.documents
                             if d.metadata_extracted.get('title'))
        docs_with_ref = sum(1 for d in report.documents
                           if d.metadata_extracted.get('reference_number'))

        session_successful = sum(1 for d in report.documents if d.status == "success")
        if session_successful > 0:
            print(f"\n  Metadata Extraction (this session):")
            print(f"    Title extracted: {docs_with_title}/{session_successful} "
                  f"({100*docs_with_title/session_successful:.0f}%)")
            print(f"    Reference # extracted: {docs_with_ref}/{session_successful} "
                  f"({100*docs_with_ref/session_successful:.0f}%)")

            # Entity boolean extraction quality
            docs_with_entities = sum(1 for d in report.documents
                                     if d.metadata_extracted.get('entity_booleans_set', 0) > 0)
            print(f"    Docs with entity booleans: {docs_with_entities}/{session_successful} "
                  f"({100*docs_with_entities/session_successful:.0f}%)")


def main():
    parser = argparse.ArgumentParser(
        description="Ingest policy PDFs with checkpoint/resume support"
    )
    parser.add_argument(
        "--local-folder",
        type=str,
        required=True,
        help="Path to folder containing PDFs"
    )
    parser.add_argument(
        "--checkpoint-file",
        type=str,
        required=True,
        help="Path to checkpoint JSON file for progress tracking"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Save checkpoint every N files (default: 50)"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing checkpoint file"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse PDFs without uploading to search index"
    )
    parser.add_argument(
        "--output-report",
        type=str,
        help="Save detailed report to JSON file"
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("RUSH POLICY INGESTION PIPELINE (with Checkpoints)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print(f"\n  Source folder: {args.local_folder}")
    print(f"  Checkpoint file: {args.checkpoint_file}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Resume mode: {args.resume}")
    print(f"  Validate only: {args.validate_only}")

    # Initialize pipeline
    pipeline = CheckpointIngestionPipeline(
        checkpoint_file=args.checkpoint_file,
        batch_size=args.batch_size,
        validate_only=args.validate_only,
    )

    # Run ingestion
    print(f"\n  Backend: Docling (TableFormer ACCURATE)")
    print()

    report = pipeline.run_ingestion(
        folder_path=args.local_folder,
        resume=args.resume,
    )

    # Print report
    print_report(report)

    # Save detailed report if requested
    if args.output_report:
        with open(args.output_report, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\n  Detailed report saved to: {args.output_report}")

    # Summary
    print("\n" + "=" * 60)
    if report.successful_documents == report.total_documents:
        print("✅ INGESTION COMPLETE - All documents processed successfully!")
    elif report.successful_documents > 0:
        print(f"⚠️  INGESTION PARTIAL - {report.successful_documents}/{report.total_documents} documents processed")
        print(f"   Run with --resume to continue from checkpoint")
    else:
        print("❌ INGESTION FAILED - No documents processed successfully")
        sys.exit(1)

    print("=" * 60)
    print("\n[DONE]")


if __name__ == "__main__":
    main()
