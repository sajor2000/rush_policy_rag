#!/usr/bin/env python3
"""
Monthly Bulk Sync Script for RUSH Policy RAG

Workflow:
1. User extracts PDFs from PolicyTech (manual step)
2. User uploads to policies-source container (manual or via this script)
3. This script detects changes via SHA-256 hash comparison
4. Only changed documents are re-indexed
5. Generates audit report for compliance

Usage:
    # Preview what would be synced (dry run)
    python scripts/monthly_bulk_sync.py --dry-run

    # Run the sync
    python scripts/monthly_bulk_sync.py

    # Upload local folder first, then sync
    python scripts/monthly_bulk_sync.py --upload-from ./monthly_pdfs/

    # Generate detailed report
    python scripts/monthly_bulk_sync.py --report monthly_report.json

    # Specify custom containers
    python scripts/monthly_bulk_sync.py --source policies-source --target policies-active
"""

import sys
import os
from pathlib import Path

# Add backend to path for imports
backend_path = Path(__file__).parent.parent / "apps" / "backend"
sys.path.insert(0, str(backend_path))

import argparse
import json
import logging
import re
from datetime import datetime
from typing import List, Tuple, Optional

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

from azure.storage.blob import BlobServiceClient
from policy_sync import PolicySyncManager, SyncReport

# Default containers
DEFAULT_SOURCE = os.environ.get("SOURCE_CONTAINER_NAME", "policies-source")
DEFAULT_TARGET = os.environ.get("CONTAINER_NAME", "policies-active")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def print_banner():
    """Print script header."""
    print(f"\n{'='*70}")
    print("MONTHLY BULK SYNC - RUSH Policy RAG")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")


def upload_local_pdfs(
    folder_path: str,
    connection_string: str,
    container_name: str
) -> int:
    """
    Upload PDFs from local folder to source container with proper resource cleanup.

    Args:
        folder_path: Path to folder containing PDFs
        connection_string: Azure Storage connection string
        container_name: Target container name

    Returns:
        Number of files uploaded

    Raises:
        IOError: If folder not found
        Exception: If upload fails critically
    """
    folder = Path(folder_path)
    if not folder.exists():
        logger.error(f"Folder not found: {folder_path}")
        print(f"ERROR: Folder not found: {folder_path}")
        sys.exit(1)

    pdf_files = list(folder.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {folder_path}")
        return 0

    print(f"\nUploading {len(pdf_files)} PDFs from {folder_path}...")
    logger.info(f"Starting upload of {len(pdf_files)} PDFs to {container_name}")

    uploaded = 0

    # Use context manager to ensure proper cleanup
    with BlobServiceClient.from_connection_string(connection_string) as blob_service:
        container_client = blob_service.get_container_client(container_name)

        # Create container if it doesn't exist
        try:
            container_client.create_container()
            print(f"  Created container: {container_name}")
            logger.info(f"Created container: {container_name}")
        except Exception as e:
            # Container already exists or other non-critical error
            logger.debug(f"Container creation skipped: {e}")

        for i, pdf_path in enumerate(pdf_files, 1):
            try:
                print(f"  [{i}/{len(pdf_files)}] {pdf_path.name}")
                with open(pdf_path, 'rb') as f:
                    content = f.read()

                blob_client = container_client.get_blob_client(pdf_path.name)
                blob_client.upload_blob(content, overwrite=True)
                uploaded += 1
                logger.debug(f"Uploaded: {pdf_path.name}")
            except Exception as e:
                logger.error(f"Failed to upload {pdf_path.name}: {e}")
                print(f"    ERROR: {e}")

    logger.info(f"Upload complete: {uploaded}/{len(pdf_files)} files to {container_name}")
    print(f"\n  Uploaded {uploaded}/{len(pdf_files)} files to {container_name}")
    return uploaded


def display_changes(
    new_files: List[str],
    changed_files: List[str],
    deleted_files: List[str],
    max_display: int = 10
):
    """Display detected changes with summary."""
    total = len(new_files) + len(changed_files) + len(deleted_files)

    print(f"\n  Summary:")
    print(f"    New documents:     {len(new_files)}")
    print(f"    Changed documents: {len(changed_files)}")
    print(f"    Deleted documents: {len(deleted_files)}")
    print(f"    Total to process:  {total}")

    if new_files:
        print(f"\n  New ({len(new_files)}):")
        for f in new_files[:max_display]:
            print(f"    + {f}")
        if len(new_files) > max_display:
            print(f"    ... and {len(new_files) - max_display} more")

    if changed_files:
        print(f"\n  Changed ({len(changed_files)}):")
        for f in changed_files[:max_display]:
            print(f"    ~ {f}")
        if len(changed_files) > max_display:
            print(f"    ... and {len(changed_files) - max_display} more")

    if deleted_files:
        print(f"\n  Deleted ({len(deleted_files)}):")
        for f in deleted_files[:max_display]:
            print(f"    - {f}")
        if len(deleted_files) > max_display:
            print(f"    ... and {len(deleted_files) - max_display} more")

    return total


def save_report(report: SyncReport, filepath: str) -> None:
    """
    Save sync report to JSON file with structured logging.

    Args:
        report: Sync report to save
        filepath: Output file path

    Raises:
        IOError: If file cannot be written
        PermissionError: If insufficient permissions
    """
    report_dict = {
        "started_at": report.started_at,
        "completed_at": report.completed_at,
        "source_container": report.source_container,
        "target_container": report.target_container,
        "documents_new": report.documents_new,
        "documents_changed": report.documents_changed,
        "documents_deleted": report.documents_deleted,
        "documents_unchanged": report.documents_unchanged,
        "chunks_created": report.chunks_created,
        "chunks_deleted": report.chunks_deleted,
        "chunks_superseded": report.chunks_superseded,
        "version_transitions": report.version_transitions,
        "errors": report.errors,
    }

    try:
        with open(filepath, 'w') as f:
            json.dump(report_dict, f, indent=2, default=str)
        logger.info(f"Report saved to: {filepath}")
        print(f"\nDetailed report saved to: {filepath}")
    except (IOError, PermissionError) as e:
        logger.error(f"Failed to save report to {filepath}: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Monthly bulk sync for RUSH policy updates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview changes without applying
  python scripts/monthly_bulk_sync.py --dry-run

  # Upload from local folder and sync
  python scripts/monthly_bulk_sync.py --upload-from ./monthly_pdfs/

  # Full sync with report generation
  python scripts/monthly_bulk_sync.py --report $(date +%Y%m)_sync_report.json

  # Use custom containers
  python scripts/monthly_bulk_sync.py --source staging --target production
        """
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying"
    )
    parser.add_argument(
        "--upload-from",
        type=str,
        metavar="FOLDER",
        help="Upload PDFs from local folder before syncing"
    )
    parser.add_argument(
        "--report",
        type=str,
        metavar="FILE",
        help="Save detailed report to JSON file"
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Source container name (default: {DEFAULT_SOURCE})"
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Target container name (default: {DEFAULT_TARGET})"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--download-date",
        type=str,
        metavar="YYYY-MM-DD",
        help="Date when PDFs were downloaded from PolicyTech (e.g., 2026-01-31). Defaults to today."
    )

    args = parser.parse_args()

    # Validate environment
    connection_string = os.environ.get("STORAGE_CONNECTION_STRING")
    if not connection_string:
        print("ERROR: STORAGE_CONNECTION_STRING environment variable not set")
        print("Please ensure your .env file is configured correctly.")
        sys.exit(1)

    print_banner()
    print(f"Source container: {args.source}")
    print(f"Target container: {args.target}")

    # Optional: upload from local folder first
    if args.upload_from:
        upload_local_pdfs(args.upload_from, connection_string, args.source)

    # Initialize sync manager
    print("\nInitializing sync manager...")
    try:
        sync_manager = PolicySyncManager()
    except Exception as e:
        print(f"ERROR: Failed to initialize sync manager: {e}")
        sys.exit(1)

    # Detect changes
    print(f"\nScanning for changes between {args.source} and {args.target}...")
    try:
        new_files, changed_files, deleted_files = sync_manager.detect_changes(
            args.source, args.target
        )
    except Exception as e:
        print(f"ERROR: Failed to detect changes: {e}")
        sys.exit(1)

    # Display changes
    total_changes = display_changes(new_files, changed_files, deleted_files)

    if total_changes == 0:
        print(f"\nNo changes detected. Index is up to date.")
        print(f"{'='*70}\n")
        return

    if args.dry_run:
        print(f"\n[DRY RUN] No changes applied. Remove --dry-run to execute.")
        print(f"{'='*70}\n")
        return

    # Confirmation prompt
    if not args.yes:
        print()
        confirm = input(f"Proceed with sync? ({total_changes} documents) [y/N]: ")
        if confirm.lower() != 'y':
            print("Sync cancelled.")
            return

    # Parse download date if provided
    download_date = None
    if args.download_date:
        try:
            download_date = datetime.strptime(args.download_date, "%Y-%m-%d")
            print(f"\nUsing download date: {download_date.strftime('%B %d, %Y')}")
        except ValueError:
            print(f"ERROR: Invalid date format '{args.download_date}'. Use YYYY-MM-DD (e.g., 2026-01-31)")
            sys.exit(1)

    # Execute sync
    print(f"\nExecuting sync...")
    logger.info(f"Starting monthly sync: {args.source} → {args.target}")

    try:
        report = sync_manager.sync_monthly(
            source_container=args.source,
            target_container=args.target,
            dry_run=False,
            download_date=download_date
        )

        # Structured logging for production monitoring
        duration_seconds = (
            datetime.fromisoformat(report.completed_at) -
            datetime.fromisoformat(report.started_at)
        ).total_seconds()

        logger.info(
            "Monthly sync completed successfully",
            extra={
                "source_container": args.source,
                "target_container": args.target,
                "documents_new": report.documents_new,
                "documents_changed": report.documents_changed,
                "documents_deleted": report.documents_deleted,
                "chunks_created": report.chunks_created,
                "chunks_superseded": report.chunks_superseded,
                "errors": len(report.errors),
                "duration_seconds": duration_seconds
            }
        )
    except Exception as e:
        logger.exception(f"Monthly sync failed: {e}")
        print(f"ERROR: Sync failed: {e}")
        sys.exit(1)

    # Display results
    print(f"\n{'='*70}")
    print("SYNC COMPLETE")
    print(f"{'='*70}")
    print(f"  New documents processed:     {report.documents_new}")
    print(f"  Updated documents processed: {report.documents_changed}")
    print(f"  Deleted documents:           {report.documents_deleted}")
    print(f"  Unchanged documents:         {report.documents_unchanged}")
    print(f"  Chunks created:              {report.chunks_created}")
    print(f"  Chunks superseded:           {report.chunks_superseded}")
    print(f"  Chunks deleted:              {report.chunks_deleted}")

    if report.errors:
        print(f"\n  Errors ({len(report.errors)}):")
        for err in report.errors[:5]:
            print(f"    - {err}")
        if len(report.errors) > 5:
            print(f"    ... and {len(report.errors) - 5} more")

    print(f"\nCompleted: {report.completed_at}")

    # Save report if requested
    if args.report:
        save_report(report, args.report)

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
