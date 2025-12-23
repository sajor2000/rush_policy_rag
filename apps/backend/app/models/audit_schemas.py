"""
Pydantic models for chat audit records.

Used for RAG quality auditing - captures all chat interactions globally
(not per-user) for analysis and monitoring.
"""

from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class AuditCitation(BaseModel):
    """Citation captured in audit record."""
    title: str
    reference_number: str
    section: Optional[str] = None
    source_file: Optional[str] = None
    reranker_score: Optional[float] = None


class ChatAuditRecord(BaseModel):
    """
    Single chat interaction audit record.

    Designed for JSONL storage - each record is one line.
    """
    # Identifiers
    audit_id: str = Field(description="UUID for this audit record")
    timestamp: datetime = Field(description="UTC timestamp of chat request")

    # Request data
    question: str = Field(description="User's question (truncated if too long)")
    filter_applies_to: Optional[str] = Field(default=None, description="Entity filter if applied")

    # Response data
    response: str = Field(description="LLM response text (truncated if too long)")
    summary: str = Field(default="", description="Quick answer summary")
    found: bool = Field(description="Whether policy was found")

    # Citations
    citations: List[AuditCitation] = Field(default_factory=list)
    chunks_used: int = Field(default=0)

    # Quality metrics
    confidence: Literal["high", "medium", "low"] = Field(default="medium")
    confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    needs_human_review: bool = Field(default=False)
    safety_flags: List[str] = Field(default_factory=list)

    # Performance
    latency_ms: int = Field(description="End-to-end response time in milliseconds")

    # Pipeline info
    pipeline_used: str = Field(default="cohere_rerank")
    search_query: Optional[str] = Field(default=None, description="Expanded query after synonym expansion")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AuditQueryRequest(BaseModel):
    """Request for querying audit logs."""
    date: Optional[str] = Field(default=None, description="Date to query (YYYY-MM-DD)")
    start_date: Optional[str] = Field(default=None, description="Start date for range query")
    end_date: Optional[str] = Field(default=None, description="End date for range query")

    # Filters
    found: Optional[bool] = Field(default=None, description="Filter by found status")
    confidence: Optional[Literal["high", "medium", "low"]] = Field(default=None)
    needs_human_review: Optional[bool] = Field(default=None)
    has_safety_flags: Optional[bool] = Field(default=None)

    # Pagination
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class AuditQueryResponse(BaseModel):
    """Response for audit log queries."""
    records: List[ChatAuditRecord]
    total_count: int
    query_date: Optional[str] = None
    date_range: Optional[dict] = None


class AuditStatsResponse(BaseModel):
    """Aggregated statistics from audit logs."""
    date: str
    total_queries: int
    found_count: int
    not_found_count: int

    confidence_breakdown: dict  # {"high": N, "medium": N, "low": N}
    needs_review_count: int

    safety_flags_count: int
    unique_safety_flags: List[str]

    avg_latency_ms: float
    p95_latency_ms: float

    pipeline_breakdown: dict  # {"cohere_rerank": N, "on_your_data": N}
