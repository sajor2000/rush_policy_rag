"""
Data models for document registry.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List
import json


class DocumentStatus(str, Enum):
    """Status of a document in the registry."""
    SYNCED = "synced"
    PENDING = "pending"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass
class DocumentRecord:
    """Record of a tracked document."""
    filename: str
    content_hash: str  # SHA256 hash of file contents
    file_size: int  # Size in bytes
    status: DocumentStatus = DocumentStatus.PENDING
    first_seen: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    last_synced: Optional[datetime] = None
    azure_etag: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "filename": self.filename,
            "content_hash": self.content_hash,
            "file_size": self.file_size,
            "status": self.status.value,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "azure_etag": self.azure_etag,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentRecord":
        """Create from dictionary."""
        return cls(
            filename=data["filename"],
            content_hash=data["content_hash"],
            file_size=data["file_size"],
            status=DocumentStatus(data["status"]),
            first_seen=datetime.fromisoformat(data["first_seen"]) if data.get("first_seen") else None,
            last_modified=datetime.fromisoformat(data["last_modified"]) if data.get("last_modified") else None,
            last_synced=datetime.fromisoformat(data["last_synced"]) if data.get("last_synced") else None,
            azure_etag=data.get("azure_etag"),
            error_message=data.get("error_message"),
        )


@dataclass
class AuditEntry:
    """Audit log entry for document changes."""
    timestamp: datetime
    action: str  # added, updated, deleted, failed
    filename: str
    user: str
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None
    details: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "filename": self.filename,
            "user": self.user,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEntry":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            action=data["action"],
            filename=data["filename"],
            user=data["user"],
            old_hash=data.get("old_hash"),
            new_hash=data.get("new_hash"),
            details=data.get("details"),
        )


@dataclass
class Manifest:
    """Document manifest with audit trail."""
    version: str = "1.0"
    azure_container: str = "policies-active"
    last_updated: Optional[datetime] = None
    documents: Dict[str, DocumentRecord] = field(default_factory=dict)
    audit_log: List[AuditEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "azure_container": self.azure_container,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "documents": {k: v.to_dict() for k, v in self.documents.items()},
            "audit_log": [e.to_dict() for e in self.audit_log],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        """Create from dictionary."""
        return cls(
            version=data.get("version", "1.0"),
            azure_container=data.get("azure_container", "policies-active"),
            last_updated=datetime.fromisoformat(data["last_updated"]) if data.get("last_updated") else None,
            documents={k: DocumentRecord.from_dict(v) for k, v in data.get("documents", {}).items()},
            audit_log=[AuditEntry.from_dict(e) for e in data.get("audit_log", [])],
        )

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "Manifest":
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @property
    def document_count(self) -> int:
        """Total number of tracked documents (excluding deleted)."""
        return sum(1 for d in self.documents.values() if d.status != DocumentStatus.DELETED)

    @property
    def synced_count(self) -> int:
        """Number of successfully synced documents."""
        return sum(1 for d in self.documents.values() if d.status == DocumentStatus.SYNCED)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    failed: List[dict] = field(default_factory=list)  # [{"filename": str, "error": str}]

    @property
    def total_changes(self) -> int:
        """Total number of documents changed."""
        return len(self.added) + len(self.updated) + len(self.deleted)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "summary": {
                "added_count": len(self.added),
                "updated_count": len(self.updated),
                "deleted_count": len(self.deleted),
                "unchanged_count": len(self.unchanged),
                "failed_count": len(self.failed),
                "total_changes": self.total_changes,
            }
        }
