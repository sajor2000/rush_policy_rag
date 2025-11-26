"""
Document Registry - Incremental document sync with audit trail for RUSH Policy RAG.

This module provides:
- DocumentRecord: Data model for tracked documents
- ManifestManager: JSON manifest with audit trail
- DocumentHasher: SHA256 content hashing
"""

from .models import DocumentRecord, AuditEntry, Manifest, DocumentStatus, SyncResult
from .hasher import DocumentHasher
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
