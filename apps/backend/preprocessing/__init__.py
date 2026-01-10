"""
Preprocessing module for RUSH policy PDF documents.

This module handles document preprocessing, chunking, and preparation
for indexing in Azure AI Search.

Main Components:
- PolicyChunker: Docling-based PDF processor with TableFormer support
- PolicyChunk: Dataclass representing a document chunk

Supporting Modules (extracted for maintainability):
- rush_metadata: Constants, enums, and metadata dataclasses
- policy_chunk: PolicyChunk dataclass definition
- checkbox_extractor: Checkbox detection for "Applies To" fields
- metadata_extractor: Title, reference number, and date extraction

Usage:
    from preprocessing import PolicyChunker, PolicyChunk

    chunker = PolicyChunker(max_chunk_size=1500)
    chunks = chunker.process_pdf("policy.pdf")

    # With detailed status
    result = chunker.process_pdf_with_status("policy.pdf")
    if result.is_success:
        for chunk in result.chunks:
            doc = chunk.to_azure_document()
"""

from .chunker import PolicyChunker
from .policy_chunk import PolicyChunk
from .rush_metadata import (
    ProcessingStatus,
    ProcessingResult,
    RUSHPolicyMetadata,
    RUSH_ENTITIES,
    ENTITY_TO_FIELD,
)

__all__ = [
    "PolicyChunker",
    "PolicyChunk",
    "ProcessingStatus",
    "ProcessingResult",
    "RUSHPolicyMetadata",
    "RUSH_ENTITIES",
    "ENTITY_TO_FIELD",
]
