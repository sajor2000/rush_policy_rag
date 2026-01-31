#!/usr/bin/env python3
"""
Hardware-Optimized Batch PDF Ingestion

Uses ProcessPoolExecutor to bypass Python GIL and maximize CPU utilization.
Auto-detects hardware or reads hardware_config.json for optimal worker count.

Usage:
    # Auto-detect hardware and process all PDFs in folder
    python scripts/optimized_batch_ingest.py --input pdf_staging/03_validated

    # Specify worker count manually
    python scripts/optimized_batch_ingest.py --input pdf_staging/03_validated --workers 8

    # Upload to Azure blob first, then index
    python scripts/optimized_batch_ingest.py --input pdf_staging/03_validated --upload-to-blob
"""

import sys
import os
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

import argparse
import json
import logging
import time
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple
from dotenv import load_dotenv

load_dotenv()

from preprocessing.chunker import PolicyChunker
from azure_policy_index import PolicySearchIndex
from azure.storage.blob import BlobServiceClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_input_folder(path_str: str) -> Path:
    """
    Validate input folder path with security checks.

    Prevents path traversal attacks and validates directory exists.
    """
    try:
        input_path = Path(path_str).resolve()  # Resolve symlinks and ".."
    except Exception as e:
        print(f"❌ Invalid path: {e}")
        sys.exit(1)

    # Must exist
    if not input_path.exists():
        print(f"❌ Path not found: {input_path}")
        sys.exit(1)

    # Must be a directory
    if not input_path.is_dir():
        print(f"❌ Path is not a directory: {input_path}")
        sys.exit(1)

    # Must be within repository (prevent path traversal)
    repo_root = Path(__file__).parent.parent.resolve()
    try:
        input_path.relative_to(repo_root)
    except ValueError:
        print(f"❌ Security: Path must be within repository: {repo_root}")
        print(f"   Attempted path: {input_path}")
        sys.exit(1)

    return input_path


@dataclass
class ProcessingResult:
    """Result from processing a single PDF."""
    filename: str
    success: bool
    chunks: int = 0
    processing_time: float = 0.0
    error: str = ""


def detect_hardware() -> int:
    """Detect optimal worker count or read from config."""
    config_path = Path(__file__).parent.parent / "hardware_config.json"

    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
            workers = config.get("recommended_workers", 4)
            print(f"📊 Using hardware config: {workers} workers")
            return workers

    # Fallback: Detect on-the-fly
    try:
        physical_cores = int(subprocess.check_output(
            ["sysctl", "-n", "hw.physicalcpu"],
            shell=False
        ).decode().strip())

        workers = max(1, physical_cores - 2)
        print(f"📊 Auto-detected: {workers} workers ({physical_cores} physical cores)")
        return workers
    except Exception as e:
        print(f"⚠️ Could not detect hardware: {e}")
        print("   Using fallback: 4 workers")
        print("   To override, use --workers N flag")
        return 4


def process_single_pdf(pdf_path: str) -> ProcessingResult:
    """
    Process a single PDF file.

    NOTE: This runs in a separate process (ProcessPoolExecutor).
    Each worker gets its own Python interpreter and memory space.
    """
    start_time = time.time()

    try:
        chunker = PolicyChunker(max_chunk_size=1500)
        result = chunker.process_pdf_with_status(pdf_path)

        if not result.chunks:
            return ProcessingResult(
                filename=Path(pdf_path).name,
                success=False,
                error="No chunks extracted"
            )

        processing_time = time.time() - start_time

        return ProcessingResult(
            filename=Path(pdf_path).name,
            success=True,
            chunks=len(result.chunks),
            processing_time=processing_time
        )
    except Exception as e:
        return ProcessingResult(
            filename=Path(pdf_path).name,
            success=False,
            error=str(e)
        )


class OptimizedBatchIngestor:
    """Hardware-optimized batch PDF processor."""

    def __init__(self, workers: int = None):
        self.workers = workers or detect_hardware()
        self.index = PolicySearchIndex()

        # Verify Azure AI Search index exists before processing
        try:
            self.index.index_client.get_index(self.index.index_name)
            print(f"✅ Connected to Azure AI Search index: {self.index.index_name}\n")
        except Exception as e:
            print(f"❌ Azure AI Search index not found: {self.index.index_name}")
            print(f"   Error: {e}")
            print(f"\n   Create the index first:")
            print(f"   cd apps/backend")
            print(f"   python azure_policy_index.py create")
            sys.exit(1)

    def upload_to_blob(self, input_folder: Path):
        """Upload PDFs to Azure Blob Storage (policies-source)."""
        pdf_files = list(input_folder.glob("*.pdf"))

        if not pdf_files:
            print("No PDFs found to upload")
            return

        print(f"\n📤 Uploading {len(pdf_files)} PDFs to policies-source...")

        conn_str = os.getenv("STORAGE_CONNECTION_STRING")
        if not conn_str:
            print("❌ Error: STORAGE_CONNECTION_STRING environment variable not set")
            print("   Required for uploading PDFs to Azure Blob Storage")
            sys.exit(1)

        container_name = os.getenv("SOURCE_CONTAINER_NAME", "policies-source")

        # Use context manager for proper resource cleanup
        with BlobServiceClient.from_connection_string(conn_str) as blob_service:
            container_client = blob_service.get_container_client(container_name)

            for i, pdf_path in enumerate(pdf_files, 1):
                try:
                    blob_client = container_client.get_blob_client(pdf_path.name)
                    with open(pdf_path, 'rb') as f:
                        blob_client.upload_blob(f, overwrite=True)
                    print(f"  [{i}/{len(pdf_files)}] {pdf_path.name}")
                except Exception as e:
                    print(f"  [{i}/{len(pdf_files)}] ❌ Failed to upload {pdf_path.name}: {e}")
                    logger.error(f"Blob upload failed for {pdf_path.name}: {e}")
                    # Continue with other files

        print(f"✅ Uploaded to {container_name}\n")

    def process_batch(self, input_folder: Path) -> Tuple[List[ProcessingResult], float]:
        """Process all PDFs in parallel using ProcessPoolExecutor."""
        pdf_files = list(input_folder.glob("*.pdf"))

        if not pdf_files:
            print(f"❌ No PDF files found in {input_folder}")
            sys.exit(1)

        print(f"\n{'='*70}")
        print(f"OPTIMIZED BATCH PROCESSING")
        print(f"{'='*70}")
        print(f"  PDFs:    {len(pdf_files)}")
        print(f"  Workers: {self.workers} (parallel processes)")
        print(f"{'='*70}\n")

        start_time = time.time()
        results = []
        WORKER_TIMEOUT = 300  # 5 minutes per PDF (generous)

        # ProcessPoolExecutor: Each worker is a separate Python process
        # Bypasses GIL for true parallel CPU processing
        try:
            with ProcessPoolExecutor(max_workers=self.workers) as executor:
                # Submit all PDFs to worker pool
                future_to_pdf = {
                    executor.submit(process_single_pdf, str(pdf)): pdf
                    for pdf in pdf_files
                }

                # Collect results as they complete
                for i, future in enumerate(as_completed(future_to_pdf), 1):
                    pdf_name = future_to_pdf[future].name

                    try:
                        # Add timeout to prevent hanging
                        result = future.result(timeout=WORKER_TIMEOUT)
                        results.append(result)

                        if result.success:
                            print(f"[{i}/{len(pdf_files)}] ✅ {result.filename}: "
                                  f"{result.chunks} chunks in {result.processing_time:.1f}s")
                        else:
                            print(f"[{i}/{len(pdf_files)}] ❌ {result.filename}: {result.error}")

                    except TimeoutError:
                        print(f"[{i}/{len(pdf_files)}] ❌ {pdf_name}: Timeout after {WORKER_TIMEOUT}s")
                        results.append(ProcessingResult(
                            filename=pdf_name,
                            success=False,
                            error=f"Processing timeout ({WORKER_TIMEOUT}s)"
                        ))
                        future.cancel()  # Try to cancel hung worker

                    except Exception as e:
                        print(f"[{i}/{len(pdf_files)}] ❌ {pdf_name}: Worker error - {e}")
                        logger.error(f"Worker error processing {pdf_name}: {e}")
                        results.append(ProcessingResult(
                            filename=pdf_name,
                            success=False,
                            error=f"Worker error: {e}"
                        ))

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user. Shutting down workers...")
            raise

        total_time = time.time() - start_time
        return results, total_time

    def print_summary(self, results: List[ProcessingResult], total_time: float):
        """Print processing summary."""
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        total_chunks = sum(r.chunks for r in successful)
        avg_time = sum(r.processing_time for r in successful) / len(successful) if successful else 0

        print(f"\n{'='*70}")
        print("PROCESSING SUMMARY")
        print(f"{'='*70}")
        print(f"Total PDFs:          {len(results)}")
        print(f"Successful:          {len(successful)}")
        print(f"Failed:              {len(failed)}")
        print(f"Total Chunks:        {total_chunks}")
        print(f"Avg Time/PDF:        {avg_time:.2f}s")
        print(f"Total Time:          {total_time:.2f}s")
        print(f"Throughput:          {len(successful) / total_time:.2f} PDFs/sec")
        print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Optimized batch PDF ingestion")
    parser.add_argument("--input", required=True, help="Input folder with PDFs")
    parser.add_argument("--workers", type=int, help="Number of parallel workers")
    parser.add_argument("--upload-to-blob", action="store_true", help="Upload to Azure blob first")
    args = parser.parse_args()

    # Validate input folder with security checks
    input_folder = validate_input_folder(args.input)

    ingestor = OptimizedBatchIngestor(workers=args.workers)

    if args.upload_to_blob:
        ingestor.upload_to_blob(input_folder)

    results, total_time = ingestor.process_batch(input_folder)
    ingestor.print_summary(results, total_time)


if __name__ == "__main__":
    main()
