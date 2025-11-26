"""
Utilities for uploading local policy documents into Azure Blob Storage.

This script uses the official Azure Storage Blobs Python SDK (>=12.27.1),
which aligns with the guidance from the Azure for Python developers docs.

Enhanced with:
- Hash-based change detection (only upload changed files)
- Manifest tracking with audit trail for compliance
- Dry-run mode to preview changes
- User attribution for audit entries
"""

from __future__ import annotations

import argparse
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from document_registry import (
    ManifestManager,
    DocumentHasher,
    DocumentRecord,
    DocumentStatus,
    SyncResult,
)


def get_blob_service_client(connection_string: str | None, account_url: str | None) -> BlobServiceClient:
    """
    Build a BlobServiceClient using either a connection string or DefaultAzureCredential.

    Args:
        connection_string: Full storage connection string.
        account_url: Optional account URL when using AAD auth.
    """
    if connection_string:
        return BlobServiceClient.from_connection_string(conn_str=connection_string)

    if not account_url:
        raise ValueError("Either STORAGE_CONNECTION_STRING or STORAGE_ACCOUNT_URL must be provided.")

    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=account_url, credential=credential)


def ensure_container(blob_service: BlobServiceClient, container_name: str) -> None:
    """
    Create the container if it does not exist (idempotent).
    """
    container_client = blob_service.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()
        print(f"Created container '{container_name}'.")
    else:
        print(f"Container '{container_name}' already exists.")


def iter_files(source: Path) -> Iterable[Path]:
    """
    Yield all files under the source directory.
    """
    if source.is_file():
        yield source
    else:
        for path in source.rglob("*"):
            if path.is_file():
                yield path


def scan_files_with_hashes(source: Path) -> dict[str, dict]:
    """
    Scan source directory and compute hashes for all files.

    Returns:
        Dictionary mapping filename to file info:
        {filename: {"path": Path, "hash": str, "size": int, "mtime": datetime}}
    """
    hasher = DocumentHasher()
    files = {}

    for file_path in iter_files(source):
        if file_path.is_file():
            content_hash, file_size = hasher.compute_hash(file_path)
            stat = file_path.stat()
            # Use relative path as the blob name
            if source.is_dir():
                blob_name = str(file_path.relative_to(source)).replace("\\", "/")
            else:
                blob_name = file_path.name

            files[blob_name] = {
                "path": file_path,
                "hash": content_hash,
                "size": file_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            }

    return files


def diff_against_manifest(
    current_files: dict[str, dict],
    manifest_manager: ManifestManager,
) -> SyncResult:
    """
    Compare current files against manifest to find changes.

    Returns:
        SyncResult with categorized files (added, updated, unchanged, deleted)
    """
    manifest = manifest_manager.load()
    result = SyncResult()

    # Check each current file
    for filename, file_info in current_files.items():
        existing = manifest.documents.get(filename)

        if existing is None:
            # New file
            result.added.append(filename)
        elif existing.status == DocumentStatus.DELETED:
            # Previously deleted, now re-added
            result.added.append(filename)
        elif existing.content_hash != file_info["hash"]:
            # Content changed
            result.updated.append(filename)
        else:
            # No change
            result.unchanged.append(filename)

    # Check for deleted files (in manifest but not in current)
    for filename, doc in manifest.documents.items():
        if doc.status != DocumentStatus.DELETED and filename not in current_files:
            result.deleted.append(filename)

    return result


def upload_with_tracking(
    source: Path,
    container_name: str,
    user: str = "system",
    dry_run: bool = False,
    overwrite: bool = True,
    connection_string: str | None = None,
    account_url: str | None = None,
) -> SyncResult:
    """
    Upload files with manifest tracking and audit trail.

    Only uploads files that are new or have changed (based on content hash).

    Args:
        source: Path to file or directory to upload
        container_name: Target blob container name
        user: Username for audit trail attribution
        dry_run: If True, preview changes without uploading
        overwrite: Whether to overwrite existing blobs
        connection_string: Azure Storage connection string
        account_url: Azure Storage account URL (for DefaultAzureCredential)

    Returns:
        SyncResult with details of what was synced
    """
    manifest_manager = ManifestManager()

    # Scan files and compute hashes
    print(f"\nScanning {source}...")
    current_files = scan_files_with_hashes(source)
    print(f"Found {len(current_files)} file(s)")

    # Compare against manifest
    diff_result = diff_against_manifest(current_files, manifest_manager)

    print(f"\nChanges detected:")
    print(f"  New:       {len(diff_result.added)}")
    print(f"  Updated:   {len(diff_result.updated)}")
    print(f"  Unchanged: {len(diff_result.unchanged)}")
    print(f"  Deleted:   {len(diff_result.deleted)}")

    if dry_run:
        # Just show what would happen
        if diff_result.added:
            print(f"\n[DRY RUN] Would add:")
            for f in sorted(diff_result.added):
                print(f"  + {f}")
        if diff_result.updated:
            print(f"\n[DRY RUN] Would update:")
            for f in sorted(diff_result.updated):
                print(f"  ~ {f}")
        if diff_result.deleted:
            print(f"\n[DRY RUN] Would mark as deleted:")
            for f in sorted(diff_result.deleted):
                print(f"  - {f}")
        return diff_result

    # Actually upload files
    if not diff_result.added and not diff_result.updated and not diff_result.deleted:
        print("\nNo changes to upload.")
        return diff_result

    blob_service = get_blob_service_client(connection_string, account_url)
    ensure_container(blob_service, container_name)
    container_client = blob_service.get_container_client(container_name)

    final_result = SyncResult(unchanged=diff_result.unchanged.copy())
    now = datetime.now(timezone.utc)

    # Upload new and updated files
    for filename in diff_result.added + diff_result.updated:
        file_info = current_files[filename]
        file_path = file_info["path"]

        content_type, _ = mimetypes.guess_type(file_path.name)
        content_settings = ContentSettings(content_type=content_type or "application/octet-stream")

        try:
            print(f"Uploading {file_path} -> {container_name}/{filename}")
            with open(file_path, "rb") as data:
                blob_client = container_client.upload_blob(
                    name=filename,
                    data=data,
                    overwrite=overwrite,
                    content_settings=content_settings,
                )
                # upload_blob returns a dict-like response, not a BlobClient
                etag = blob_client.get("etag", "") if isinstance(blob_client, dict) else ""

            # Record in manifest
            record = DocumentRecord(
                filename=filename,
                content_hash=file_info["hash"],
                file_size=file_info["size"],
                status=DocumentStatus.SYNCED,
                last_modified=file_info["mtime"],
                last_synced=now,
                azure_etag=etag,
            )
            manifest_manager.add_or_update_document(record, user)

            if filename in diff_result.added:
                final_result.added.append(filename)
            else:
                final_result.updated.append(filename)

        except Exception as e:
            error_msg = str(e)
            print(f"  ERROR: {error_msg}")
            manifest_manager.mark_failed(filename, error_msg, user)
            final_result.failed.append({"filename": filename, "error": error_msg})

    # Mark deleted files
    for filename in diff_result.deleted:
        manifest_manager.mark_deleted(filename, user)
        final_result.deleted.append(filename)
        print(f"Marked as deleted: {filename}")

    # Save manifest
    manifest_manager.save()

    print(f"\nSync complete:")
    print(f"  Added:   {len(final_result.added)}")
    print(f"  Updated: {len(final_result.updated)}")
    print(f"  Deleted: {len(final_result.deleted)}")
    print(f"  Failed:  {len(final_result.failed)}")

    return final_result


def show_status() -> None:
    """Show current manifest statistics."""
    manager = ManifestManager()
    stats = manager.get_statistics()

    print("\nDocument Registry Status")
    print("=" * 50)
    print(f"Container:       {stats['azure_container']}")
    print(f"Last updated:    {stats['last_updated'] or 'Never'}")
    print(f"\nDocuments:")
    print(f"  Total tracked: {stats['total_documents']}")
    print(f"  Active:        {stats['active_documents']}")
    print(f"  Synced:        {stats['synced_documents']}")
    print(f"\nStatus breakdown:")
    for status, count in stats['status_breakdown'].items():
        print(f"  {status}: {count}")
    print(f"\nAudit log entries: {stats['audit_log_entries']}")


def show_history(limit: int = 50, since: Optional[str] = None) -> None:
    """Show recent audit history."""
    manager = ManifestManager()
    manager.load()

    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    entries = manager.get_audit_entries(since=since_dt, limit=limit)

    print("\nAudit History")
    print("=" * 50)

    if not entries:
        print("No audit entries found.")
        return

    print(f"Showing {len(entries)} entries:\n")

    for entry in entries:
        timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        action_symbol = {
            "added": "+",
            "updated": "~",
            "deleted": "-",
            "failed": "!",
        }.get(entry.action, "?")

        print(f"[{timestamp}] {action_symbol} {entry.action.upper():8} {entry.filename}")
        print(f"             User: {entry.user}")
        if entry.old_hash and entry.new_hash:
            print(f"             Hash: {entry.old_hash[:12]}... -> {entry.new_hash[:12]}...")
        elif entry.new_hash:
            print(f"             Hash: {entry.new_hash[:12]}...")
        if entry.details:
            print(f"             Details: {entry.details}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload policy documents to Azure Blob Storage with audit trail.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would be uploaded (dry run)
  python blob_ingest.py --source ../../rag_small --dry-run

  # Upload with audit trail
  python blob_ingest.py --source ../../rag_small --user "jsmith"

  # Check current status
  python blob_ingest.py --status

  # View upload history
  python blob_ingest.py --history --limit 20
        """,
    )

    # Mode flags (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--status",
        action="store_true",
        help="Show current manifest statistics",
    )
    mode_group.add_argument(
        "--history",
        action="store_true",
        help="Show recent audit history",
    )

    # Source/upload arguments
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/policies"),
        help="Path to a file or directory containing policy documents (default: data/policies)",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("CONTAINER_NAME", "policies-active"),
        help="Target container name (default: env CONTAINER_NAME or 'policies-active')",
    )
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("STORAGE_CONNECTION_STRING"),
        help="Storage connection string (default: env STORAGE_CONNECTION_STRING)",
    )
    parser.add_argument(
        "--account-url",
        default=os.environ.get("STORAGE_ACCOUNT_URL"),
        help="Account URL when using DefaultAzureCredential (e.g., https://<name>.blob.core.windows.net)",
    )

    # Upload options
    parser.add_argument(
        "--user", "-u",
        default=os.environ.get("USER", "system"),
        help="Username for audit trail (default: $USER or 'system')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without uploading",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite existing blobs",
    )

    # History options
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum audit entries to show (default: 50)",
    )
    parser.add_argument(
        "--since",
        help="Show entries since date (YYYY-MM-DD)",
    )

    args = parser.parse_args()

    # Handle modes
    if args.status:
        show_status()
        return

    if args.history:
        show_history(limit=args.limit, since=args.since)
        return

    # Default: upload mode
    if not args.source.exists():
        raise FileNotFoundError(f"Source path '{args.source}' does not exist.")

    upload_with_tracking(
        source=args.source.resolve(),
        container_name=args.container,
        user=args.user,
        dry_run=args.dry_run,
        overwrite=not args.no_overwrite,
        connection_string=args.connection_string,
        account_url=args.account_url,
    )


if __name__ == "__main__":
    main()

