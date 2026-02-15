"""
Document Registry - Incremental document sync with audit trail for RUSH Policy RAG.

This module provides:
- DocumentRecord: Data model for tracked documents
- ManifestManager: JSON manifest with audit trail
- DocumentHasher: SHA256 content hashing
"""

from .hasher import DocumentHasher
from .models import AuditEntry, DocumentRecord, DocumentStatus, Manifest, SyncResult
from .registry import ManifestManager

__all__ = [
    "DocumentRecord",
    "AuditEntry",
    "Manifest",
    "DocumentStatus",
    "SyncResult",
    "DocumentHasher",
    "ManifestManager",
]
