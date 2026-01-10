"""
PolicyChunk dataclass for RUSH policy PDF processing.

This module contains the PolicyChunk dataclass which represents a single
chunk of text from a RUSH policy PDF, optimized for Azure AI Search indexing.

Key principle: The 'text' field contains EXACT text from the PDF,
never modified, summarized, or paraphrased.

Extracted from chunker.py as part of tech debt refactoring.
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PolicyChunk:
    """
    A chunk optimized for LITERAL text retrieval with full citation.

    Key principle: The 'text' field contains EXACT text from the PDF,
    never modified, summarized, or paraphrased.

    Attributes:
        chunk_id: Unique identifier for this chunk
        policy_title: Title of the source policy
        reference_number: Policy reference number (e.g., "528")
        section_number: Section number within policy
        section_title: Section title
        text: EXACT text from PDF - never modified
        date_updated: Last update date
        applies_to: Comma-separated entity codes
        source_file: Source PDF filename
        char_count: Character count for the chunk
        content_hash: MD5 hash of text for deduplication
    """
    chunk_id: str
    policy_title: str
    reference_number: str
    section_number: str
    section_title: str
    text: str                    # EXACT text - never modified
    date_updated: str
    applies_to: str              # Comma-separated string (backward compatibility)
    source_file: str
    char_count: int
    content_hash: str = field(default="")
    document_owner: str = field(default="")
    date_approved: str = field(default="")

    # Entity-specific boolean filters (for efficient Azure Search filtering)
    applies_to_rumc: bool = field(default=False)   # Rush University Medical Center
    applies_to_rumg: bool = field(default=False)   # Rush University Medical Group
    applies_to_rmg: bool = field(default=False)    # Rush Medical Group
    applies_to_roph: bool = field(default=False)   # Rush Oak Park Hospital
    applies_to_rcmc: bool = field(default=False)   # Rush Copley Medical Center
    applies_to_rch: bool = field(default=False)    # Rush Children's Hospital
    applies_to_roppg: bool = field(default=False)  # Rush Oak Park Physicians Group
    applies_to_rcmg: bool = field(default=False)   # Rush Copley Medical Group
    applies_to_ru: bool = field(default=False)     # Rush University

    # Hierarchical chunking fields
    chunk_level: str = field(default="semantic")   # "document" | "section" | "semantic"
    parent_chunk_id: Optional[str] = field(default=None)
    chunk_index: int = field(default=0)

    # Enhanced metadata fields
    category: Optional[str] = field(default=None)
    subcategory: Optional[str] = field(default=None)
    regulatory_citations: Optional[str] = field(default=None)
    related_policies: Optional[str] = field(default=None)

    # Page number for PDF navigation
    page_number: Optional[int] = field(default=None)  # 1-indexed page number

    # Version control fields (for monthly update tracking)
    version_number: str = field(default="1.0")           # Policy version (e.g., "1.0", "2.0")
    version_date: Optional[str] = field(default=None)    # ISO datetime when version was created
    effective_date: Optional[str] = field(default=None)  # When policy takes effect
    expiration_date: Optional[str] = field(default=None) # When policy expires (if any)
    policy_status: str = field(default="ACTIVE")         # ACTIVE, SUPERSEDED, RETIRED, DRAFT
    superseded_by: Optional[str] = field(default=None)   # Version that replaced this (e.g., "2.0")
    version_sequence: int = field(default=1)             # Numeric sequence for sorting

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.md5(self.text.encode()).hexdigest()[:12]

    def get_citation(self) -> str:
        """Generate citation for RAG response."""
        ref_part = f"Ref: {self.reference_number}" if self.reference_number else "No Ref #"
        if self.section_number and self.section_title:
            return f"{self.policy_title} ({ref_part}), Section {self.section_number}. {self.section_title}"
        return f"{self.policy_title} ({ref_part})"

    def to_azure_document(self) -> dict:
        """
        Format for Azure AI Search index.

        Recommended index schema:
        - id: Edm.String (key)
        - content: Edm.String (searchable)
        - title: Edm.String (searchable, filterable)
        - reference_number: Edm.String (filterable)
        - section: Edm.String (searchable, filterable)
        - citation: Edm.String (retrievable)
        - applies_to: Edm.String (filterable)
        - applies_to_*: Edm.Boolean (filterable, facetable) - 9 entity fields
        - date_updated: Edm.String (filterable)
        - content_vector: Collection(Edm.Single) (for vector search)
        - chunk_level: Edm.String (filterable)
        - parent_chunk_id: Edm.String (filterable)
        - chunk_index: Edm.Int32 (sortable)
        - category: Edm.String (filterable, facetable)
        - subcategory: Edm.String (filterable, facetable)
        - regulatory_citations: Edm.String (searchable)
        - related_policies: Edm.String (searchable)
        """
        # Azure requires alphanumeric IDs
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', self.chunk_id)
        return {
            "id": safe_id,
            "content": self.text,
            "title": self.policy_title,
            "reference_number": self.reference_number,
            "section": f"{self.section_number}. {self.section_title}" if self.section_number else "",
            "citation": self.get_citation(),
            "applies_to": self.applies_to,
            "date_updated": self.date_updated,
            "source_file": self.source_file,
            "content_hash": self.content_hash,
            "document_owner": self.document_owner,
            "date_approved": self.date_approved,
            # Entity-specific boolean filters
            "applies_to_rumc": self.applies_to_rumc,
            "applies_to_rumg": self.applies_to_rumg,
            "applies_to_rmg": self.applies_to_rmg,
            "applies_to_roph": self.applies_to_roph,
            "applies_to_rcmc": self.applies_to_rcmc,
            "applies_to_rch": self.applies_to_rch,
            "applies_to_roppg": self.applies_to_roppg,
            "applies_to_rcmg": self.applies_to_rcmg,
            "applies_to_ru": self.applies_to_ru,
            # Hierarchical chunking fields
            "chunk_level": self.chunk_level,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_index": self.chunk_index,
            # Enhanced metadata fields
            "category": self.category,
            "subcategory": self.subcategory,
            "regulatory_citations": self.regulatory_citations,
            "related_policies": self.related_policies,
            # Page number for PDF navigation
            "page_number": self.page_number,
            # Version control fields
            "version_number": self.version_number,
            "version_date": self.version_date,
            "effective_date": self.effective_date,
            "expiration_date": self.expiration_date,
            "policy_status": self.policy_status,
            "superseded_by": self.superseded_by,
            "version_sequence": self.version_sequence,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "chunk_id": self.chunk_id,
            "policy_title": self.policy_title,
            "reference_number": self.reference_number,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "text": self.text,
            "date_updated": self.date_updated,
            "applies_to": self.applies_to,
            "source_file": self.source_file,
            "char_count": self.char_count,
            "content_hash": self.content_hash,
            "document_owner": self.document_owner,
            "date_approved": self.date_approved,
            "citation": self.get_citation(),
            # Entity-specific boolean filters
            "applies_to_rumc": self.applies_to_rumc,
            "applies_to_rumg": self.applies_to_rumg,
            "applies_to_rmg": self.applies_to_rmg,
            "applies_to_roph": self.applies_to_roph,
            "applies_to_rcmc": self.applies_to_rcmc,
            "applies_to_rch": self.applies_to_rch,
            "applies_to_roppg": self.applies_to_roppg,
            "applies_to_rcmg": self.applies_to_rcmg,
            "applies_to_ru": self.applies_to_ru,
            # Hierarchical chunking fields
            "chunk_level": self.chunk_level,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_index": self.chunk_index,
            # Enhanced metadata fields
            "category": self.category,
            "subcategory": self.subcategory,
            "regulatory_citations": self.regulatory_citations,
            "related_policies": self.related_policies,
            # Page number for PDF navigation
            "page_number": self.page_number,
            # Version control fields (for monthly update tracking)
            "version_number": self.version_number,
            "version_date": self.version_date,
            "effective_date": self.effective_date,
            "expiration_date": self.expiration_date,
            "policy_status": self.policy_status,
            "superseded_by": self.superseded_by,
            "version_sequence": self.version_sequence,
        }
