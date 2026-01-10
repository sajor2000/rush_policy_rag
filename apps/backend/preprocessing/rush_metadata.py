"""
RUSH Policy metadata classes and constants.

This module contains dataclasses and enums for RUSH policy PDF processing:
- ProcessingStatus: Enum for PDF processing status codes
- ProcessingResult: Result container with status and error handling
- RUSHPolicyMetadata: Structured metadata from policy headers
- Constants for entity codes and checkbox detection

Extracted from chunker.py as part of tech debt refactoring.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from preprocessing.policy_chunk import PolicyChunk


# ============================================================================
# Constants
# ============================================================================

# All RUSH entity codes that may appear in checkboxes
RUSH_ENTITIES = ['RUMC', 'RUMG', 'RMG', 'ROPH', 'RCMC', 'RCH', 'ROPPG', 'RCMG', 'RU']

# Unicode characters for checked/unchecked states
CHECKED_CHARS = r'[\u2612\u2611\u2713\u2714\u25A0\u2718Xx☒☑✓✔■]'
UNCHECKED_CHARS = r'[\u2610\u25A1\u25CB☐]'

# Entity code to field name mapping
ENTITY_TO_FIELD = {
    'RUMC': 'applies_to_rumc',
    'RUMG': 'applies_to_rumg',
    'RMG': 'applies_to_rmg',
    'ROPH': 'applies_to_roph',
    'RCMC': 'applies_to_rcmc',
    'RCH': 'applies_to_rch',
    'ROPPG': 'applies_to_roppg',
    'RCMG': 'applies_to_rcmg',
    'RU': 'applies_to_ru',
}


# ============================================================================
# Processing Status Enum
# ============================================================================

class ProcessingStatus(str, Enum):
    """Status codes for PDF processing results."""
    SUCCESS = "success"
    EMPTY_DOCUMENT = "empty_document"
    FILE_NOT_FOUND = "file_not_found"
    DOCLING_UNAVAILABLE = "docling_unavailable"
    PROCESSING_ERROR = "processing_error"


# ============================================================================
# Processing Result
# ============================================================================

@dataclass
class ProcessingResult:
    """Result of PDF processing with detailed status information.

    Use this to distinguish between:
    - Success with chunks
    - Empty document (0 chunks but not an error)
    - Actual failures (Docling unavailable, file not found, processing error)

    Examples:
        >>> result = chunker.process_pdf_with_status("policy.pdf")
        >>> if result.is_error:
        ...     print(f"Failed: {result.status.value} - {result.error_message}")
        >>> elif result.is_empty:
        ...     print("Document had no extractable content")
        >>> else:
        ...     documents = [chunk.to_azure_document() for chunk in result.chunks]
    """
    chunks: List["PolicyChunk"]
    status: ProcessingStatus
    error_message: Optional[str] = None
    source_file: str = ""

    @property
    def is_success(self) -> bool:
        """True if processing succeeded (may have 0 chunks for empty docs)."""
        return self.status == ProcessingStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        """True if processing failed due to an actual error."""
        return self.status in (
            ProcessingStatus.FILE_NOT_FOUND,
            ProcessingStatus.DOCLING_UNAVAILABLE,
            ProcessingStatus.PROCESSING_ERROR
        )

    @property
    def is_empty(self) -> bool:
        """True if document was processed but had no extractable content."""
        return self.status == ProcessingStatus.EMPTY_DOCUMENT


# ============================================================================
# RUSH Policy Metadata
# ============================================================================

@dataclass
class RUSHPolicyMetadata:
    """Structured metadata extracted from RUSH policy header table.

    This dataclass holds all metadata fields extracted from the standard
    RUSH policy PDF header, including the "Applies To" entity checkboxes.

    Attributes:
        title: Policy title
        reference_number: Policy reference number (e.g., "528")
        document_owner: Department/person responsible
        approvers: List of approvers
        date_created: Creation date
        date_approved: Approval date
        date_updated: Last update date
        review_due: Next review date
        applies_to: List of entity codes (e.g., ["RUMC", "RUMG"])
        applies_to_*: Boolean flags for each entity
    """
    title: str = ""
    reference_number: str = ""
    document_owner: str = ""
    approvers: str = ""
    date_created: str = ""
    date_approved: str = ""
    date_updated: str = ""
    review_due: str = ""
    applies_to: List[str] = field(default_factory=list)

    # Entity-specific booleans (populated from applies_to list)
    applies_to_rumc: bool = field(default=False)
    applies_to_rumg: bool = field(default=False)
    applies_to_rmg: bool = field(default=False)
    applies_to_roph: bool = field(default=False)
    applies_to_rcmc: bool = field(default=False)
    applies_to_rch: bool = field(default=False)
    applies_to_roppg: bool = field(default=False)
    applies_to_rcmg: bool = field(default=False)
    applies_to_ru: bool = field(default=False)

    # Enhanced metadata
    category: Optional[str] = field(default=None)
    subcategory: Optional[str] = field(default=None)
    regulatory_citations: Optional[str] = field(default=None)
    related_policies: Optional[str] = field(default=None)

    @property
    def applies_to_str(self) -> str:
        """Format applies_to as comma-separated string for index."""
        return ", ".join(self.applies_to) if self.applies_to else "All"

    def set_entity_booleans_from_list(self) -> None:
        """Set entity boolean fields based on applies_to list."""
        for entity in self.applies_to:
            field_name = ENTITY_TO_FIELD.get(entity.upper())
            if field_name:
                setattr(self, field_name, True)
