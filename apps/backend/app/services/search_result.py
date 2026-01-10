"""
Search result dataclass for Azure AI Search policy retrieval.

This module contains the SearchResult dataclass used throughout the RAG pipeline
to represent policy chunks retrieved from Azure AI Search.

Extracted from azure_policy_index.py as part of tech debt refactoring.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchResult:
    """
    Represents a single search result from Azure AI Search.

    Contains all metadata needed for RAG context and citation generation.
    Optimized for literal text retrieval with full policy metadata.

    Attributes:
        content: The chunk text content
        citation: Pre-formatted citation string
        title: Policy title
        section: Section within the policy
        applies_to: Comma-separated entity codes (e.g., "RUMC, RUMG")
        date_updated: Last update date string
        score: Azure AI Search relevance score
        reference_number: Policy reference number (e.g., "486")
        reranker_score: Semantic reranker score (if enabled)
        source_file: Source PDF filename
        document_owner: Department/person responsible for policy
        date_approved: Approval date string
        applies_to_*: Boolean flags for each RUSH entity
        chunk_level: Hierarchical level ("document", "section", "semantic")
        parent_chunk_id: ID of parent chunk in hierarchy
        chunk_index: Position in chunk sequence
        page_number: PDF page number for navigation
        category: Policy category
        subcategory: Policy subcategory
        regulatory_citations: Referenced regulations
        related_policies: Cross-referenced policy IDs
    """

    # Core content fields
    content: str = ""
    citation: str = ""
    title: str = ""
    section: str = ""
    applies_to: str = ""
    date_updated: str = ""

    # Search scoring
    score: float = 0.0
    reference_number: str = ""
    reranker_score: Optional[float] = None

    # Source tracking
    source_file: str = ""
    document_owner: str = ""
    date_approved: str = ""

    # Entity-specific booleans for O(1) filtering
    applies_to_rumc: bool = False
    applies_to_rumg: bool = False
    applies_to_rmg: bool = False
    applies_to_roph: bool = False
    applies_to_rcmc: bool = False
    applies_to_rch: bool = False
    applies_to_roppg: bool = False
    applies_to_rcmg: bool = False
    applies_to_ru: bool = False

    # Hierarchical chunking fields
    chunk_level: str = "semantic"
    parent_chunk_id: Optional[str] = None
    chunk_index: int = 0

    # Page number for PDF navigation
    page_number: Optional[int] = None

    # Enhanced metadata
    category: Optional[str] = None
    subcategory: Optional[str] = None
    regulatory_citations: Optional[str] = None
    related_policies: Optional[str] = None

    def format_for_rag(self) -> str:
        """
        Format result for RAG prompt context with full metadata.

        Creates a clearly delimited policy chunk with all relevant metadata
        for the LLM to use in generating responses.

        Returns:
            Formatted string suitable for RAG context injection
        """
        # Extract reference from citation for cleaner display
        ref_part = "N/A"
        if '(' in self.citation and ')' in self.citation:
            try:
                ref_part = self.citation.split('(')[1].split(')')[0]
            except (IndexError, AttributeError):
                ref_part = "N/A"

        reference_display = self.reference_number or ref_part

        return f"""┌────────────────────────────────────────────────────────────┐
│ POLICY: {self.title}
│ Reference: {reference_display}
│ Section: {self.section}
│ Applies To: {self.applies_to}
│ Document Owner: {self.document_owner or 'N/A'}
│ Updated: {self.date_updated} | Approved: {self.date_approved or 'N/A'}
│ Source: {self.source_file}
└────────────────────────────────────────────────────────────┘

{self.content}
"""

    def get_entity_list(self) -> list[str]:
        """
        Get list of RUSH entities this policy applies to.

        Returns:
            List of entity codes (e.g., ["RUMC", "RUMG", "ROPH"])
        """
        entities = []
        if self.applies_to_rumc:
            entities.append("RUMC")
        if self.applies_to_rumg:
            entities.append("RUMG")
        if self.applies_to_rmg:
            entities.append("RMG")
        if self.applies_to_roph:
            entities.append("ROPH")
        if self.applies_to_rcmc:
            entities.append("RCMC")
        if self.applies_to_rch:
            entities.append("RCH")
        if self.applies_to_roppg:
            entities.append("ROPPG")
        if self.applies_to_rcmg:
            entities.append("RCMG")
        if self.applies_to_ru:
            entities.append("RU")
        return entities


def format_rag_context(results: list[SearchResult]) -> str:
    """
    Format search results as context for RAG prompt with relevance indicators.

    This creates a context block that encourages literal retrieval:
    - Each chunk is clearly delimited with relevance score
    - Citations are prominently displayed
    - No synthesis encouraged

    Args:
        results: List of SearchResult objects from search

    Returns:
        Formatted context string for RAG prompt injection
    """
    if not results:
        return "No relevant policy documents found."

    context_parts = []
    for i, result in enumerate(results, 1):
        # Show relevance score to help LLM prioritize
        if result.reranker_score:
            confidence = f"Relevance: {result.reranker_score:.2f}"
        else:
            confidence = f"Score: {result.score:.2f}"

        context_parts.append(f"""
═══════════════════════════════════════════════════════════════
 POLICY CHUNK {i} ({confidence})
═══════════════════════════════════════════════════════════════
{result.format_for_rag()}
""")

    return "\n".join(context_parts)
