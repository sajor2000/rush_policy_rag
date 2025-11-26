"""
Manifest manager for document registry.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from .models import Manifest, DocumentRecord, AuditEntry, DocumentStatus


class ManifestManager:
    """
    Manages the document manifest JSON file with audit trail.

    The manifest tracks:
    - All documents with their SHA256 hashes
    - Sync status (synced, pending, deleted, failed)
    - Timestamps (first seen, last modified, last synced)
    - Full audit log of all changes
    """

    DEFAULT_MANIFEST_PATH = Path(__file__).parent.parent / "data" / "document_manifest.json"

    def __init__(self, manifest_path: Optional[Path] = None):
        """
        Initialize the manifest manager.

        Args:
            manifest_path: Path to the manifest file. Defaults to data/document_manifest.json
        """
        self.manifest_path = manifest_path or self.DEFAULT_MANIFEST_PATH
        self.manifest: Optional[Manifest] = None

    def load(self) -> Manifest:
        """
        Load manifest from file or create a new one.

        Returns:
            The loaded or newly created manifest
        """
        if self.manifest_path.exists():
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.manifest = Manifest.from_dict(data)
        else:
            self.manifest = Manifest(
                last_updated=datetime.utcnow(),
                azure_container=os.environ.get("CONTAINER_NAME", "policies-active"),
            )
        return self.manifest

    def save(self) -> None:
        """Save manifest to file."""
        if self.manifest is None:
            raise ValueError("No manifest loaded. Call load() first.")

        # Ensure directory exists
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

        # Update timestamp
        self.manifest.last_updated = datetime.utcnow()

        # Write to file
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write(self.manifest.to_json(indent=2))

    def get_document(self, filename: str) -> Optional[DocumentRecord]:
        """
        Get a document record by filename.

        Args:
            filename: Name of the document file

        Returns:
            DocumentRecord if found, None otherwise
        """
        if self.manifest is None:
            self.load()
        return self.manifest.documents.get(filename)

    def add_or_update_document(
        self,
        record: DocumentRecord,
        user: str = "system",
    ) -> str:
        """
        Add a new document or update existing one.

        Args:
            record: Document record to add/update
            user: Username for audit trail

        Returns:
            Action taken: "added" or "updated"
        """
        if self.manifest is None:
            self.load()

        existing = self.manifest.documents.get(record.filename)

        if existing is None:
            # New document
            record.first_seen = datetime.utcnow()
            self.manifest.documents[record.filename] = record
            self._add_audit_entry(
                action="added",
                filename=record.filename,
                user=user,
                new_hash=record.content_hash,
            )
            return "added"
        else:
            # Update existing
            old_hash = existing.content_hash
            record.first_seen = existing.first_seen  # Preserve first seen
            self.manifest.documents[record.filename] = record
            self._add_audit_entry(
                action="updated",
                filename=record.filename,
                user=user,
                old_hash=old_hash,
                new_hash=record.content_hash,
            )
            return "updated"

    def mark_synced(
        self,
        filename: str,
        azure_etag: Optional[str] = None,
    ) -> None:
        """
        Mark a document as successfully synced.

        Args:
            filename: Name of the document file
            azure_etag: ETag from Azure blob (optional)
        """
        if self.manifest is None:
            self.load()

        doc = self.manifest.documents.get(filename)
        if doc:
            doc.status = DocumentStatus.SYNCED
            doc.last_synced = datetime.utcnow()
            if azure_etag:
                doc.azure_etag = azure_etag
            doc.error_message = None

    def mark_deleted(
        self,
        filename: str,
        user: str = "system",
    ) -> None:
        """
        Mark a document as deleted.

        Args:
            filename: Name of the document file
            user: Username for audit trail
        """
        if self.manifest is None:
            self.load()

        doc = self.manifest.documents.get(filename)
        if doc:
            old_hash = doc.content_hash
            doc.status = DocumentStatus.DELETED
            self._add_audit_entry(
                action="deleted",
                filename=filename,
                user=user,
                old_hash=old_hash,
            )

    def mark_failed(
        self,
        filename: str,
        error_message: str,
        user: str = "system",
    ) -> None:
        """
        Mark a document sync as failed.

        Args:
            filename: Name of the document file
            error_message: Error description
            user: Username for audit trail
        """
        if self.manifest is None:
            self.load()

        doc = self.manifest.documents.get(filename)
        if doc:
            doc.status = DocumentStatus.FAILED
            doc.error_message = error_message
            self._add_audit_entry(
                action="failed",
                filename=filename,
                user=user,
                details=error_message,
            )

    def _add_audit_entry(
        self,
        action: str,
        filename: str,
        user: str,
        old_hash: Optional[str] = None,
        new_hash: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Add an entry to the audit log."""
        entry = AuditEntry(
            timestamp=datetime.utcnow(),
            action=action,
            filename=filename,
            user=user,
            old_hash=old_hash,
            new_hash=new_hash,
            details=details,
        )
        self.manifest.audit_log.append(entry)

    def get_audit_entries(
        self,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """
        Get audit log entries.

        Args:
            since: Only return entries after this datetime
            limit: Maximum number of entries to return

        Returns:
            List of audit entries (most recent first)
        """
        if self.manifest is None:
            self.load()

        entries = self.manifest.audit_log

        if since:
            entries = [e for e in entries if e.timestamp > since]

        # Sort by timestamp descending (most recent first)
        entries = sorted(entries, key=lambda e: e.timestamp, reverse=True)

        return entries[:limit]

    def get_documents_by_status(
        self,
        status: DocumentStatus,
    ) -> List[DocumentRecord]:
        """
        Get all documents with a specific status.

        Args:
            status: Status to filter by

        Returns:
            List of matching document records
        """
        if self.manifest is None:
            self.load()

        return [d for d in self.manifest.documents.values() if d.status == status]

    def get_statistics(self) -> Dict:
        """
        Get manifest statistics.

        Returns:
            Dictionary with statistics
        """
        if self.manifest is None:
            self.load()

        status_counts = {}
        for status in DocumentStatus:
            status_counts[status.value] = sum(
                1 for d in self.manifest.documents.values() if d.status == status
            )

        return {
            "total_documents": len(self.manifest.documents),
            "active_documents": self.manifest.document_count,
            "synced_documents": self.manifest.synced_count,
            "status_breakdown": status_counts,
            "audit_log_entries": len(self.manifest.audit_log),
            "last_updated": self.manifest.last_updated.isoformat() if self.manifest.last_updated else None,
            "azure_container": self.manifest.azure_container,
        }
