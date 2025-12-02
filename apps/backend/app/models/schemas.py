from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str
    filter_applies_to: Optional[str] = None  # e.g., "RMG" to filter by entity


class EvidenceItem(BaseModel):
    snippet: str
    citation: str
    title: str
    reference_number: str = ""
    section: str = ""
    applies_to: str = ""
    document_owner: Optional[str] = None
    date_updated: Optional[str] = None
    date_approved: Optional[str] = None
    source_file: Optional[str] = None
    score: Optional[float] = None
    reranker_score: Optional[float] = None
    match_type: Optional[str] = None  # "verified" (exact match), "related" (fallback search)


class ChatResponse(BaseModel):
    response: str
    summary: str
    evidence: List[EvidenceItem] = Field(default_factory=list)
    raw_response: Optional[str] = None
    sources: List[dict] = Field(default_factory=list)
    chunks_used: int = 0
    found: bool = True
    # Healthcare safety fields - critical for high-risk environments
    confidence: Literal["high", "medium", "low"] = "medium"
    confidence_score: Optional[float] = None  # Raw score 0.0-1.0
    needs_human_review: bool = False  # Flag for low-confidence responses
    safety_flags: List[str] = Field(default_factory=list)  # Any safety concerns


class SearchRequest(BaseModel):
    query: str
    top: int = 5
    filter_applies_to: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[dict]
    query: str
    count: int
