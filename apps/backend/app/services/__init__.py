"""
RUSH Policy RAG Services

This module contains all service classes for the policy retrieval system:

Core Services:
- ChatService: Main orchestrator for RAG queries
- OnYourDataService: Azure OpenAI integration with vector search
- CohereRerankService: Cross-encoder reranking for negation-aware search

Supporting Services:
- SynonymService: Query-time synonym expansion
- CacheService: Multi-layer response caching
- ChatAuditService: Query logging and analytics
- InstanceSearchService: Within-policy search functionality

Query Processing (extracted from chat_service.py):
- query_processor: Intent detection and policy resolution
- query_validation: Input validation and entity extraction
- query_enhancer: Query expansion and refinement
- confidence_calculator: Response confidence scoring
- device_disambiguator: Medical device term disambiguation
- entity_ranking: Location-based policy prioritization
- ranking_utils: Scoring utilities for search results
- response_formatter: Output formatting for chat responses

Search Infrastructure:
- search_result: SearchResult dataclass
- search_synonyms: Synonym map for Azure AI Search

Note: Imports are intentionally lazy to avoid circular import issues.
Import services directly from their modules:
    from app.services.chat_service import ChatService
    from app.services.search_result import SearchResult
"""

# Only import standalone modules that don't have circular dependencies
# Core services should be imported directly from their modules to avoid
# circular import issues with azure_policy_index.py
from .search_result import SearchResult, format_rag_context

__all__ = [
    # Search types (safe to import at package level)
    "SearchResult",
    "format_rag_context",
]
