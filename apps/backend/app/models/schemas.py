from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    filter_applies_to: Optional[str] = None  # e.g., "RMG" to filter by entity


class EvidenceItem(BaseModel):
    snippet: str
    citation: str
    title: str
    reference_number: str = ""
    policy_number: str = ""
    section: str = ""
    applies_to: str = ""
    document_owner: Optional[str] = None
    date_updated: Optional[str] = None
    date_approved: Optional[str] = None
    source_file: Optional[str] = None
    page_number: Optional[int] = None  # 1-indexed page number for PDF navigation
    score: Optional[float] = None
    reranker_score: Optional[float] = None
    match_type: Optional[str] = (
        None  # "verified" (exact match), "related" (fallback search)
    )


class SearchResultItem(BaseModel):
    """Search result with complete metadata for frontend attribution."""

    # Core content
    citation: str
    content: str
    title: str
    section: str = ""
    reference_number: str = ""
    policy_number: str = ""
    applies_to: str = ""

    # Dates and ownership
    date_updated: Optional[str] = None
    date_approved: Optional[str] = None
    document_owner: Optional[str] = None
    source_file: Optional[str] = None

    # PDF navigation (CRITICAL for page jump feature)
    page_number: Optional[int] = None  # 1-indexed

    # Enhanced metadata
    category: Optional[str] = None
    subcategory: Optional[str] = None
    regulatory_citations: Optional[str] = None
    related_policies: Optional[str] = None

    # Hierarchical chunking
    chunk_level: str = "semantic"
    parent_chunk_id: Optional[str] = None
    chunk_index: int = 0

    # Entity booleans (for filtering)
    applies_to_rumc: bool = False
    applies_to_rumg: bool = False
    applies_to_rmg: bool = False
    applies_to_roph: bool = False
    applies_to_rcmc: bool = False
    applies_to_rch: bool = False
    applies_to_roppg: bool = False
    applies_to_rcmg: bool = False
    applies_to_ru: bool = False

    # Search scoring
    score: Optional[float] = None
    reranker_score: Optional[float] = None


class ChatResponse(BaseModel):
    response: str
    summary: str
    evidence: List[EvidenceItem] = Field(default_factory=list)
    raw_response: Optional[str] = None
    sources: List[dict] = Field(default_factory=list)
    chunks_used: int = 0
    found: bool = True
    # Healthcare safety fields - critical for high-risk environments
    confidence: Literal["high", "medium", "low", "clarification_needed"] = "medium"
    confidence_score: Optional[float] = None  # Raw score 0.0-1.0
    needs_human_review: bool = False  # Flag for low-confidence responses
    safety_flags: List[str] = Field(default_factory=list)  # Any safety concerns
    # Ambiguity clarification field - for queries needing user input
    clarification: Optional[Dict[str, Any]] = (
        None  # Contains message, options, ambiguous_term
    )
    # Audit field - expanded query after synonym expansion (for RAG improvement analysis)
    search_query: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top: int = Field(default=5, ge=1, le=50)
    filter_applies_to: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    query: str
    count: int


# ============================================================================
# Instance Search Models - Find all occurrences of a term within a policy
# ============================================================================


class InstanceSearchRequest(BaseModel):
    """Request for searching term instances within a specific policy."""

    policy_ref: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Policy reference number (e.g., '528', 'HR-B 13.00')",
    )
    search_term: str = Field(
        ..., min_length=1, max_length=200, description="Term to search for"
    )
    case_sensitive: bool = Field(
        default=False, description="Whether search is case-sensitive"
    )


class TermInstance(BaseModel):
    """A single instance of a term found within a policy."""

    page_number: Optional[int] = Field(
        default=None, description="1-indexed page number in PDF"
    )
    section: str = Field(default="", description="Section number where term appears")
    section_title: str = Field(default="", description="Section title")
    context: str = Field(description="Surrounding text context (~200 chars)")
    position: int = Field(description="Character position within chunk")
    chunk_id: str = Field(description="ID of the chunk containing this instance")
    highlight_start: int = Field(description="Start position of term within context")
    highlight_end: int = Field(description="End position of term within context")


class InstanceSearchResponse(BaseModel):
    """Response containing all instances of a term within a policy."""

    policy_title: str
    policy_ref: str
    search_term: str
    total_instances: int
    instances: List[TermInstance]
    source_file: Optional[str] = None
