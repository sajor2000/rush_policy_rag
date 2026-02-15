#!/usr/bin/env python3
"""
Optimized Policy Ingestion Script with Parallel Processing

Features:
- Parallel processing (configurable workers, default 75% of CPU cores)
- Visual progress bar with ETA, rate, and current file
- Checkpoint/resume every N files (default: 100)
- Detailed progress tracking and reporting

Usage:
    # Run full ingestion with defaults (75% CPU, checkpoint every 100)
    python scripts/optimized_ingest.py --local-folder "../../SimpleExport_27236_2026_01_22"

    # Resume from checkpoint
    python scripts/optimized_ingest.py --local-folder "../../SimpleExport_27236_2026_01_22" --resume

    # Custom settings
    python scripts/optimized_ingest.py --local-folder "..." --workers 8 --checkpoint-every 50
"""

import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Corporate proxy SSL fix
try:
    pass
except ImportError:
    pass

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# Try to import tqdm for progress bar
try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    print("Note: Install tqdm for better progress display: pip install tqdm")


@dataclass
class FileResult:
    """Result from processing a single file."""

    filename: str
    status: str  # "success", "failed"
    chunks_created: int = 0
    chunks_uploaded: int = 0
    error_message: str = ""
    processing_time_sec: float = 0


@dataclass
class Checkpoint:
    """Checkpoint data for resume support."""

    total_files: int = 0
    processed_files: List[str] = field(default_factory=list)
    failed_files: List[Dict] = field(default_factory=list)
    chunks_created: int = 0
    chunks_uploaded: int = 0
    start_time: str = ""
    last_save_time: str = ""

    def save(self, path: str):
        self.last_save_time = datetime.now().isoformat()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Checkpoint":
        if not os.path.exists(path):
            return cls()
        with open(path, "r") as f:
            return cls(**json.load(f))


def process_single_file(args: Tuple[str, str]) -> FileResult:
    """
    Process a single PDF file. This runs in a worker process.

    Args:
        args: Tuple of (pdf_path, source_file_name)

    Returns:
        FileResult with processing outcome
    """
    pdf_path, source_file = args
    start_time = time.time()

    # Import inside function for multiprocessing
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    load_dotenv(env_path)

    from azure_policy_index import PolicySearchIndex
    from preprocessing.chunker import PolicyChunker

    result = FileResult(filename=source_file, status="pending")

    try:
        # Initialize (creates new instances per worker)
        chunker = PolicyChunker()
        index = PolicySearchIndex()

        # Process PDF
        chunks = chunker.process_pdf(pdf_path)
        result.chunks_created = len(chunks)

        if chunks:
            # Delete existing chunks for this file
            index.delete_by_source_file(source_file)

            # Upload chunks
            stats = index.upload_chunks(chunks, batch_size=100)
            result.chunks_uploaded = stats["uploaded"]

            if stats["failed"] > 0:
                result.error_message = f"{stats['failed']} chunks failed"

            result.status = "success"
        else:
            result.status = "failed"
            result.error_message = "No chunks extracted"

    except Exception as e:
        result.status = "failed"
        result.error_message = str(e)

    result.processing_time_sec = time.time() - start_time
    return result


class OptimizedIngestionPipeline:
    """
    Optimized ingestion pipeline with parallel processing and progress tracking.
    """

    def __init__(
        self,
        checkpoint_file: str,
        workers: int = None,
        checkpoint_every: int = 100,
    ):
        self.checkpoint_file = checkpoint_file
        self.checkpoint_every = checkpoint_every

        # Default to 75% of CPU cores, minimum 2
        if workers is None:
            cpu_count = multiprocessing.cpu_count()
            workers = max(2, int(cpu_count * 0.75))
        self.workers = workers

        self.checkpoint = Checkpoint()

    def run(
        self,
        folder_path: str,
        resume: bool = False,
        skip_first_n: int = 0,
    ) -> Dict:
        """
        Run optimized parallel ingestion.

        Args:
            folder_path: Path to folder containing PDFs
            resume: Whether to resume from checkpoint
            skip_first_n: Skip first N files (for testing incremental batches)
        """
        folder = Path(folder_path)

        # Get all PDFs sorted
        all_pdfs = sorted([f for f in folder.glob("*.pdf") if f.is_file()])
        total_files = len(all_pdfs)

        print(f"\n{'='*60}")
        print("OPTIMIZED POLICY INGESTION")
        print(f"{'='*60}")
        print(f"  Source folder: {folder_path}")
        print(f"  Total PDFs found: {total_files}")
        print(f"  Workers: {self.workers} (75% of {multiprocessing.cpu_count()} CPUs)")
        print(f"  Checkpoint every: {self.checkpoint_every} files")
        print(f"  Resume mode: {resume}")

        # Load or initialize checkpoint
        if resume and os.path.exists(self.checkpoint_file):
            self.checkpoint = Checkpoint.load(self.checkpoint_file)
            already_done = set(self.checkpoint.processed_files)
            already_failed = set(f["file"] for f in self.checkpoint.failed_files)
            print(
                f"  Resuming from: {len(already_done)} processed, {len(already_failed)} failed"
            )
        else:
            self.checkpoint = Checkpoint(
                total_files=total_files, start_time=datetime.now().isoformat()
            )
            already_done = set()
            already_failed = set()

        # Filter to unprocessed files
        pdfs_to_process = [
            p
            for p in all_pdfs
            if p.name not in already_done and p.name not in already_failed
        ]

        # Apply skip if specified
        if skip_first_n > 0:
            pdfs_to_process = pdfs_to_process[skip_first_n:]

        print(f"  Files to process: {len(pdfs_to_process)}")
        print(f"{'='*60}\n")

        if not pdfs_to_process:
            print("No files to process!")
            return self._build_report()

        # Prepare work items
        work_items = [(str(p), p.name) for p in pdfs_to_process]

        # Track progress
        results = []
        processed_since_checkpoint = 0

        # Process with parallel workers
        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(process_single_file, item): item for item in work_items
            }

            # Progress tracking
            if HAS_TQDM:
                pbar = tqdm(
                    total=len(work_items),
                    desc="Processing",
                    unit="file",
                    ncols=100,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                )

            # Process completed futures
            for future in as_completed(futures):
                result = future.result()
                results.append(result)

                # Update checkpoint data
                if result.status == "success":
                    self.checkpoint.processed_files.append(result.filename)
                    self.checkpoint.chunks_created += result.chunks_created
                    self.checkpoint.chunks_uploaded += result.chunks_uploaded
                else:
                    self.checkpoint.failed_files.append(
                        {"file": result.filename, "error": result.error_message}
                    )

                processed_since_checkpoint += 1

                # Update progress bar
                if HAS_TQDM:
                    status = "✓" if result.status == "success" else "✗"
                    pbar.set_postfix_str(f"{status} {result.filename[:30]}...")
                    pbar.update(1)
                else:
                    total_done = len(self.checkpoint.processed_files) + len(
                        self.checkpoint.failed_files
                    )
                    print(
                        f"  [{total_done}/{total_files}] {result.status}: {result.filename[:40]}"
                    )

                # Save checkpoint periodically
                if processed_since_checkpoint >= self.checkpoint_every:
                    self.checkpoint.save(self.checkpoint_file)
                    processed_since_checkpoint = 0
                    if HAS_TQDM:
                        pbar.set_description("Processing (saved checkpoint)")

            if HAS_TQDM:
                pbar.close()

        # Final checkpoint save
        self.checkpoint.save(self.checkpoint_file)

        return self._build_report()

    def _build_report(self) -> Dict:
        """Build final report from checkpoint data."""
        return {
            "total_files": self.checkpoint.total_files,
            "processed": len(self.checkpoint.processed_files),
            "failed": len(self.checkpoint.failed_files),
            "chunks_created": self.checkpoint.chunks_created,
            "chunks_uploaded": self.checkpoint.chunks_uploaded,
            "start_time": self.checkpoint.start_time,
            "end_time": datetime.now().isoformat(),
            "checkpoint_file": self.checkpoint_file,
        }


def print_report(report: Dict):
    """Print final ingestion report."""
    print(f"\n{'='*60}")
    print("INGESTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Total files: {report['total_files']}")
    print(f"  Processed: {report['processed']}")
    print(f"  Failed: {report['failed']}")
    print(f"  Chunks created: {report['chunks_created']:,}")
    print(f"  Chunks uploaded: {report['chunks_uploaded']:,}")

    if report["processed"] > 0:
        success_rate = (
            report["processed"] / (report["processed"] + report["failed"]) * 100
        )
        print(f"  Success rate: {success_rate:.1f}%")

    print(f"\n  Checkpoint saved to: {report['checkpoint_file']}")
    print(f"{'='*60}\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Optimized parallel policy ingestion")
    parser.add_argument(
        "--local-folder", type=str, required=True, help="Folder containing PDFs"
    )
    parser.add_argument(
        "--checkpoint-file",
        type=str,
        default="/private/tmp/ingestion_checkpoint.json",
        help="Checkpoint file path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: 75%% of CPUs)",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=100,
        help="Save checkpoint every N files (default: 100)",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument(
        "--skip-first",
        type=int,
        default=0,
        help="Skip first N files (for incremental testing)",
    )

    args = parser.parse_args()

    pipeline = OptimizedIngestionPipeline(
        checkpoint_file=args.checkpoint_file,
        workers=args.workers,
        checkpoint_every=args.checkpoint_every,
    )

    report = pipeline.run(
        folder_path=args.local_folder,
        resume=args.resume,
        skip_first_n=args.skip_first,
    )

    print_report(report)


if __name__ == "__main__":
    main()
