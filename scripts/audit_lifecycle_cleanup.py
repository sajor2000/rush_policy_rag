#!/usr/bin/env python3
"""
Audit Lifecycle Cleanup - Enforce retention policy on chat audit JSONL blobs.

Deletes chat-audit blobs older than CHAT_AUDIT_RETENTION_DAYS (default 90).
Blob path format: chat-audit/YYYY/MM/DD.jsonl

Usage:
    # Dry run (preview deletions without removing anything)
    python scripts/audit_lifecycle_cleanup.py --dry-run

    # Enforce default 90-day retention
    python scripts/audit_lifecycle_cleanup.py

    # Custom retention period
    python scripts/audit_lifecycle_cleanup.py --retention-days 60

    # Explicit connection string
    python scripts/audit_lifecycle_cleanup.py --connection-string "..."
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# Add backend to path for config imports
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

load_dotenv(REPO_ROOT / ".env")

from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CONTAINER_NAME = settings.CHAT_AUDIT_CONTAINER  # "chat-audit"


def parse_blob_date(blob_name: str) -> datetime | None:
    """Parse date from blob path like '2026/01/15.jsonl' -> datetime(2026,1,15)."""
    try:
        # Strip .jsonl extension and parse YYYY/MM/DD
        date_str = blob_name.replace(".jsonl", "")
        return datetime.strptime(date_str, "%Y/%m/%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def run_cleanup(
    connection_string: str,
    retention_days: int,
    dry_run: bool,
) -> dict:
    """
    Delete chat-audit blobs older than retention_days.

    Returns summary dict with counts and details.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    logger.info(f"Retention policy: {retention_days} days")
    logger.info(f"Cutoff date: {cutoff.strftime('%Y-%m-%d')}")
    if dry_run:
        logger.info("DRY RUN — no blobs will be deleted")

    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service.get_container_client(CONTAINER_NAME)

    if not container_client.exists():
        logger.warning(f"Container '{CONTAINER_NAME}' does not exist. Nothing to clean.")
        return {"deleted": 0, "retained": 0, "skipped": 0, "details": []}

    deleted = 0
    retained = 0
    skipped = 0
    details: list[dict] = []

    for blob in container_client.list_blobs():
        if not blob.name.endswith(".jsonl"):
            skipped += 1
            continue

        blob_date = parse_blob_date(blob.name)
        if blob_date is None:
            logger.warning(f"Could not parse date from blob: {blob.name}")
            skipped += 1
            continue

        age_days = (datetime.now(timezone.utc) - blob_date).days
        size_bytes = getattr(blob, "size", 0) or 0

        if blob_date < cutoff:
            detail = {
                "blob": blob.name,
                "date": blob_date.strftime("%Y-%m-%d"),
                "age_days": age_days,
                "size_bytes": size_bytes,
                "action": "DELETE" if not dry_run else "WOULD_DELETE",
            }
            details.append(detail)

            if not dry_run:
                container_client.delete_blob(blob.name)
                logger.info(f"Deleted: {blob.name} (age={age_days}d, size={size_bytes}B)")
            else:
                logger.info(f"Would delete: {blob.name} (age={age_days}d, size={size_bytes}B)")
            deleted += 1
        else:
            retained += 1

    return {
        "deleted": deleted,
        "retained": retained,
        "skipped": skipped,
        "cutoff_date": cutoff.strftime("%Y-%m-%d"),
        "retention_days": retention_days,
        "dry_run": dry_run,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce audit log retention policy by deleting expired JSONL blobs"
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=settings.CHAT_AUDIT_RETENTION_DAYS,
        help=f"Days to retain audit logs (default: {settings.CHAT_AUDIT_RETENTION_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without removing anything",
    )
    parser.add_argument(
        "--connection-string",
        default=settings.STORAGE_CONNECTION_STRING,
        help="Azure Storage connection string (default: from .env)",
    )
    args = parser.parse_args()

    if not args.connection_string:
        logger.error("STORAGE_CONNECTION_STRING not set. Provide via .env or --connection-string.")
        return 1

    if args.retention_days < 1:
        logger.error("--retention-days must be >= 1")
        return 1

    result = run_cleanup(
        connection_string=args.connection_string,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
    )

    # Print summary
    print()
    print("=" * 60)
    print("AUDIT LIFECYCLE CLEANUP SUMMARY")
    print("=" * 60)
    print(f"  Retention:  {result['retention_days']} days")
    print(f"  Cutoff:     {result['cutoff_date']}")
    print(f"  Dry Run:    {result['dry_run']}")
    print(f"  Deleted:    {result['deleted']}")
    print(f"  Retained:   {result['retained']}")
    print(f"  Skipped:    {result['skipped']}")
    if result["details"]:
        total_bytes = sum(d["size_bytes"] for d in result["details"])
        print(f"  Space freed: {total_bytes / 1024:.1f} KB")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
