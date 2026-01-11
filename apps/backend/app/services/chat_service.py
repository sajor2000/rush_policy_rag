import logging
import asyncio
import re
from typing import Optional, List, Dict, Tuple, Set
from collections import defaultdict
from app.models.schemas import ChatRequest, ChatResponse, EvidenceItem
from app.core.prompts import RISEN_PROMPT, NOT_FOUND_MESSAGE, LLM_UNAVAILABLE_MESSAGE
from app.core.security import build_applies_to_filter
from azure_policy_index import PolicySearchIndex, format_rag_context, SearchResult
from app.services.on_your_data_service import OnYourDataService, OnYourDataResult
from app.services.cohere_rerank_service import CohereRerankService, RerankResult
from app.services.synonym_service import get_synonym_service, QueryExpansion
from app.services.citation_verifier import get_citation_verifier, CitationVerifier, VerificationResult
from app.services.citation_formatter import get_citation_formatter
from app.services.safety_validator import get_safety_validator, ResponseSafetyValidator
from app.services.corrective_rag import get_corrective_rag_service, CorrectiveRAGService
from app.services.self_reflective_rag import get_self_reflective_service, SelfReflectiveRAGService
from app.services.query_decomposer import get_query_decomposer, QueryDecomposer
from app.services.cache_service import get_cache_service, CacheService
from app.core.config import settings

# Extracted modules (tech debt refactoring)
# Aliased with underscore prefix for backward compatibility
from app.services.query_processor import (
    detect_instance_search_intent as _detect_instance_search_intent,
    resolve_policy_identifier as _resolve_policy_identifier,
    strip_references_from_negative_response as _strip_references_from_negative_response,
    is_refusal_response as _is_refusal_response,
    truncate_verbatim as _truncate_verbatim,
    normalize_policy_title as _normalize_policy_title,
    get_policy_hint,
    POLICY_HINTS,
)
from app.services.ranking_utils import (
    apply_mmr_diversification as _apply_mmr_diversification,
    is_surge_capacity_policy as _is_surge_capacity_policy,
    apply_surge_capacity_penalty as _apply_surge_capacity_penalty,
    apply_mmr_to_rerank_results as _apply_mmr_to_rerank_results,
)
from app.services.entity_ranking import (
    extract_entity_mentions as _extract_entity_mentions,
    apply_location_boost as _apply_location_boost,
    detect_pediatric_context as _detect_pediatric_context,
    is_pediatric_policy as _is_pediatric_policy,
    apply_population_ranking as _apply_population_ranking,
    get_all_entity_codes,
    is_entity_specific_query,
    ENTITY_PATTERNS,
    PEDIATRIC_KEYWORD_PATTERNS,
    LOCATION_CONTEXT_PATTERNS,
)
from app.services.response_formatter import (
    extract_reference_identifier as _extract_reference_identifier,
    derive_source_file as _derive_source_file,
    extract_quick_answer as _extract_quick_answer,
    format_answer_with_citations as _format_answer_with_citations,
    build_supporting_evidence,
)
from app.services.query_validation import (
    is_not_found_response as _is_not_found_response_standalone,
    is_out_of_scope_query as _is_out_of_scope_query_standalone,
    is_multi_policy_query as _is_multi_policy_query_standalone,
    is_adversarial_query as _is_adversarial_query_standalone,
    is_unclear_query as _is_unclear_query_standalone,
    NOT_FOUND_PHRASES,
    ALWAYS_OUT_OF_SCOPE,
    MULTI_POLICY_INDICATORS,
    POLICY_TOPIC_KEYWORDS,
    ADVERSARIAL_PATTERNS,
    ADVERSARIAL_REFUSAL_MESSAGE,
    UNCLEAR_QUERY_MESSAGE,
)
from app.services.device_disambiguator import (
    detect_device_ambiguity as _detect_device_ambiguity_standalone,
    AMBIGUOUS_DEVICE_TERMS,
)
from app.services.query_enhancer import (
    generate_query_variants as _generate_query_variants_standalone,
    reciprocal_rank_fusion as _reciprocal_rank_fusion_standalone,
    normalize_location_context as _normalize_location_context_standalone,
    normalize_query_punctuation as _normalize_query_punctuation_standalone,
    apply_policy_hints as _apply_policy_hints_standalone,
)
from app.services.confidence_calculator import (
    filter_by_score_window as _filter_by_score_window_standalone,
    calculate_response_confidence as _calculate_response_confidence_standalone,
    confidence_level_from_score as _confidence_level_from_score_standalone,
    boost_confidence_with_grounding as _boost_confidence_with_grounding_standalone,
    should_return_not_found as _should_return_not_found_standalone,
)

from openai import AzureOpenAI
import httpx
import os

logger = logging.getLogger(__name__)

# Query validation constants are now imported from app.services.query_validation:
# NOT_FOUND_PHRASES, ALWAYS_OUT_OF_SCOPE, MULTI_POLICY_INDICATORS,
# POLICY_TOPIC_KEYWORDS, ADVERSARIAL_PATTERNS, ADVERSARIAL_REFUSAL_MESSAGE, UNCLEAR_QUERY_MESSAGE

# NOT_FOUND_OR_REFUSAL_PATTERNS, _strip_references_from_negative_response,
# _is_refusal_response, _truncate_verbatim, TITLE_NORMALIZATION_RULES,
# and _normalize_policy_title are now imported from query_processor module

# _apply_mmr_diversification, _is_surge_capacity_policy, _apply_surge_capacity_penalty
# are now imported from ranking_utils module

# ENTITY_PATTERNS, LOCATION_CONTEXT_PATTERNS are now imported from entity_ranking module

# _extract_entity_mentions, _apply_location_boost, _detect_pediatric_context,
# _is_pediatric_policy, _apply_population_ranking, _apply_mmr_to_rerank_results,
# PEDIATRIC_KEYWORD_PATTERNS, PEDIATRIC_POLICY_TITLE_PATTERNS
# are now imported from entity_ranking module

# _extract_reference_identifier, _derive_source_file, _extract_quick_answer,
# _format_answer_with_citations, and build_supporting_evidence
# are now imported from response_formatter module


class ChatService:
    """
    Chat service for policy Q&A using Azure OpenAI "On Your Data".

    Uses vectorSemanticHybrid search for best quality:
    - Vector search (text-embedding-3-large)
    - BM25 + 132 synonym rules
    - L2 Semantic Reranking
    """

    # AMBIGUOUS_DEVICE_TERMS is now imported from app.services.device_disambiguator

    def __init__(
        self,
        search_index: PolicySearchIndex,
        on_your_data_service: Optional[OnYourDataService] = None,
        cohere_rerank_service: Optional[CohereRerankService] = None
    ):
        self.search_index = search_index
        self.on_your_data_service = on_your_data_service
        self.cohere_rerank_service = cohere_rerank_service

        # Initialize cache service for latency optimization
        try:
            self.cache_service = get_cache_service()
            if self.cache_service.enabled:
                logger.info("Cache service initialized for response caching")
        except Exception as e:
            logger.warning(f"Cache service unavailable: {e}")
            self.cache_service = None

        # Initialize synonym service for query expansion
        try:
            self.synonym_service = get_synonym_service()
            logger.info("Synonym service initialized for query expansion")
        except Exception as e:
            logger.warning(f"Synonym service unavailable: {e}")
            self.synonym_service = None

        # Initialize Azure OpenAI client for Cohere rerank pipeline
        # (Cohere reranks, then we use regular chat completions for LLM)
        self._openai_client = None
        if cohere_rerank_service and cohere_rerank_service.is_configured:
            aoai_endpoint = os.environ.get("AOAI_ENDPOINT")
            aoai_key = os.environ.get("AOAI_API_KEY") or os.environ.get("AOAI_API")
            if aoai_endpoint and aoai_key:
                http_client = httpx.Client(
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                )
                self._openai_client = AzureOpenAI(
                    azure_endpoint=aoai_endpoint,
                    api_key=aoai_key,
                    api_version=os.environ.get("AOAI_API_VERSION", "2024-08-01-preview"),
                    http_client=http_client
                )
                logger.info("Azure OpenAI client initialized for Cohere rerank pipeline")

    # ========================================================================
    # FIX 1: Expanded "not found" detection
    # ========================================================================
    def _is_not_found_response(self, answer_text: str) -> bool:
        """Detect if LLM response indicates no information found."""
        return _is_not_found_response_standalone(answer_text, NOT_FOUND_MESSAGE)

    # ========================================================================
    # FIX 2: Out-of-scope pre-query validation (DATA-DRIVEN)
    # ========================================================================
    def _is_out_of_scope_query(self, query: str) -> bool:
        """Detect queries about topics with NO policies in the database."""
        return _is_out_of_scope_query_standalone(query)

    # ========================================================================
    # FIX 5: Multi-policy query detection (Enhanced)
    # ========================================================================
    def _is_multi_policy_query(self, query: str) -> bool:
        """Detect if query likely spans multiple policies."""
        return _is_multi_policy_query_standalone(query)

    # ========================================================================
    # Device Ambiguity Detection - Prevents noisy results from vague queries
    # ========================================================================
    def detect_device_ambiguity(self, query: str) -> Optional[Dict]:
        """Detect if query contains ambiguous medical device shorthand."""
        return _detect_device_ambiguity_standalone(query)

    # ========================================================================
    # FIX 7: Dynamic search parameters based on query complexity
    # ========================================================================
    def _get_search_params(self, query: str) -> dict:
        """
        Determine optimal search parameters based on query characteristics.
        
        Per Microsoft best practices:
        - Lower strictness for queries with acronyms (reduces false negatives)
        - Higher top_n_documents for multi-policy queries (more comprehensive)
        - Standard parameters for complex queries without acronyms
        """
        words = query.split()
        word_count = len(words)
        
        # Known healthcare acronyms that benefit from lower strictness
        healthcare_acronyms = {
            'sbar', 'rrt', 'icu', 'ed', 'er', 'cpr', 'dnr', 'hipaa', 'pca',
            'picc', 'npo', 'prn', 'stat', 'vte', 'rumc', 'rumg', 'roph',
            'epic', 'lvad', 'ecmo', 'pacu', 'nicu', 'picu', 'bls', 'acls'
        }
        
        # Check if query contains any healthcare acronyms
        query_lower = query.lower()
        has_acronym = any(acr in query_lower for acr in healthcare_acronyms)
        
        # Check for short acronym-only queries (e.g., "SBAR", "RRT")
        is_acronym_query = word_count <= 2 and any(
            w.isupper() and len(w) >= 2 for w in words
        )
        
        # HEALTHCARE SAFETY: Always use strictness=5 (maximum)
        # This ensures responses are strictly grounded in retrieved documents.
        # Lower strictness allows model knowledge to contaminate responses.
        
        # Multi-policy queries need more documents
        if self._is_multi_policy_query(query):
            return {
                'strictness': 5,  # HEALTHCARE: Maximum strictness
                'top_n_documents': 100  # More documents for comprehensive coverage
            }
        
        # Acronym queries - still use max strictness but more docs
        if has_acronym or is_acronym_query or word_count <= 3:
            return {
                'strictness': 5,  # HEALTHCARE: Maximum strictness
                'top_n_documents': 75  # More docs to compensate for strict grounding
            }
        
        # Standard queries
        return {
            'strictness': 5,  # HEALTHCARE: Maximum strictness
            'top_n_documents': 50
        }

    def _get_cohere_top_n(self, query: str) -> int:
        """
        Dynamic top_n for Cohere reranking based on query complexity.

        Multi-policy queries need more results to ensure comprehensive coverage.
        Simple queries can use fewer results for precision.
        """
        if self._is_multi_policy_query(query):
            return 10  # More results for multi-policy queries
        if len(query.split()) <= 3:
            return 5   # Fewer for simple/short queries
        return 7       # Default for standard queries

    def filter_by_score_window(
        self,
        reranked: List[RerankResult],
        query: str,
        window_threshold: float = 0.6
    ) -> List[RerankResult]:
        """Filter reranked results to keep only docs within a relative score window."""
        return _filter_by_score_window_standalone(reranked, query, window_threshold)

    # ========================================================================
    # HEALTHCARE SAFETY: Confidence Scoring for Response Routing
    # ========================================================================
    def _calculate_response_confidence(
        self,
        reranked: List[RerankResult],
        has_evidence: bool = True
    ) -> Tuple[float, str]:
        """Calculate confidence score for healthcare response routing."""
        return _calculate_response_confidence_standalone(reranked, has_evidence)

    def _confidence_level_from_score(self, score: float) -> str:
        """Map a numeric confidence score to qualitative buckets."""
        return _confidence_level_from_score_standalone(score)

    def _boost_confidence_with_grounding(
        self,
        confidence_score: float,
        evidence_items: List[EvidenceItem],
        verification: Optional[VerificationResult] = None
    ) -> float:
        """Boost confidence using grounding signals."""
        return _boost_confidence_with_grounding_standalone(confidence_score, evidence_items, verification)

    def _should_return_not_found(
        self,
        confidence_score: float,
        confidence_level: str,
        has_evidence: bool
    ) -> bool:
        """Determine if response should be 'not found' based on confidence."""
        return _should_return_not_found_standalone(confidence_score, confidence_level, has_evidence)

    # ========================================================================
    # P0: HyDE (Hypothetical Document Embeddings) Query Enhancement
    # ========================================================================
    async def _generate_hyde_query(self, query: str) -> str:
        """
        Generate a hypothetical policy document snippet for better retrieval.
        
        HyDE works by asking the LLM to generate a hypothetical answer to the query,
        then using that hypothetical document for embedding-based search. This helps
        bridge the vocabulary gap between user queries and policy documents.
        
        Example:
        - Query: "What is SBAR?"
        - HyDE output: "SBAR is a communication framework used during patient hand-offs.
          It stands for Situation, Background, Assessment, Recommendation..."
        - Combined: "What is SBAR? SBAR is a communication framework..."
        """
        try:
            # Use a fast model for HyDE generation (GPT-4o-mini or similar)
            aoai_endpoint = os.environ.get("AOAI_ENDPOINT", "")
            aoai_key = os.environ.get("AOAI_API_KEY", "") or os.environ.get("AOAI_API", "")
            
            if not aoai_endpoint or not aoai_key:
                logger.debug("HyDE skipped: Azure OpenAI not configured")
                return query
            
            client = AzureOpenAI(
                azure_endpoint=aoai_endpoint,
                api_key=aoai_key,
                api_version="2024-06-01",
                timeout=5.0  # Fast timeout for HyDE
            )
            
            hyde_prompt = f"""You are a hospital policy expert. Generate a brief (2-3 sentences) policy document excerpt that would answer this question. Write as if quoting from an official hospital policy document.

Question: {query}

Policy excerpt:"""
            
            # HEALTHCARE SAFETY: HyDE DISABLED - uses model knowledge, not database facts
            # This is a hallucination vector in healthcare contexts.
            # HyDE generates hypothetical answers that could introduce fabricated medical guidance.
            logger.debug("HyDE disabled for healthcare safety - using original query")
            return query
            
        except asyncio.TimeoutError:
            logger.debug("HyDE generation timed out, using original query")
            return query
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}, using original query")
            return query

    # ========================================================================
    # P1: Multi-Query Fusion with Reciprocal Rank Fusion (RRF)
    # ========================================================================
    def _generate_query_variants(self, query: str) -> List[str]:
        """Generate query variants for multi-query fusion."""
        return _generate_query_variants_standalone(query)

    def _reciprocal_rank_fusion(
        self,
        result_lists: List[List[Dict]],
        k: int = 60
    ) -> List[Dict]:
        """Merge multiple result lists using Reciprocal Rank Fusion (RRF)."""
        return _reciprocal_rank_fusion_standalone(result_lists, k)

    # ========================================================================
    # FIX 6: Adversarial query detection
    # ========================================================================
    def _is_adversarial_query(self, query: str) -> bool:
        """Detect adversarial queries that try to bypass safety protocols."""
        return _is_adversarial_query_standalone(query)

    def _is_unclear_query(self, query: str) -> bool:
        """Detect unclear queries that need clarification before processing."""
        return _is_unclear_query_standalone(query)

    def _expand_query(self, query: str) -> tuple[str, Optional[QueryExpansion]]:
        """
        Expand user query with synonyms for better search accuracy.

        Handles:
        - Location context normalization (strips generic location phrases)
        - Medical abbreviations (ED → emergency department)
        - Common misspellings (cathater → catheter)
        - Rush-specific terms (RUMC → Rush University Medical Center)
        - Hospital codes (code blue → cardiac arrest)

        Returns:
            Tuple of (expanded_query, expansion_details)
        """
        # Check expansion cache first (uses normalized query as key)
        if self.cache_service and self.cache_service.enabled:
            cached = self.cache_service.get_expansion(query)
            if cached is not None:
                logger.debug(f"[CACHE HIT] Expansion cache hit for: {query[:50]}...")
                return cached

        # Normalize location context first (strip generic phrases like "in a patient room")
        # The extracted context is logged inside _normalize_location_context; we only need the query
        query, _ = self._normalize_location_context(query)

        # Normalize punctuation (possessives, smart quotes, whitespace)
        query = self._normalize_query_punctuation(query)

        if not self.synonym_service:
            return query, None

        try:
            expansion = self.synonym_service.expand_query(query)

            if expansion.expanded_query != query:
                logger.info(
                    f"Query expanded: '{query}' → '{expansion.expanded_query}' "
                    f"(abbrevs: {len(expansion.abbreviations_expanded)}, "
                    f"misspellings: {len(expansion.misspellings_corrected)})"
                )

            result = (expansion.expanded_query, expansion)

            # Cache the expansion result
            if self.cache_service and self.cache_service.enabled:
                self.cache_service.set_expansion(query, expansion.expanded_query, expansion)

            return result
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}")
            return query, None

    def _normalize_location_context(self, query: str) -> tuple[str, Optional[str]]:
        """Normalize generic location context phrases."""
        return _normalize_location_context_standalone(query)

    def _normalize_query_punctuation(self, query: str) -> str:
        """Normalize query punctuation for consistent matching."""
        return _normalize_query_punctuation_standalone(query)

    def _apply_policy_hints(self, query: str) -> Tuple[str, List[dict]]:
        """Append domain hints and collect target references."""
        return _apply_policy_hints_standalone(query)

    # ========================================================================
    # Instance Search Handler - "find X in policy Y" queries
    # ========================================================================
    async def _handle_instance_search(
        self,
        search_term: str,
        policy_id: str
    ) -> ChatResponse:
        """
        Handle queries that want to find specific text/sections within a policy.

        Examples:
        - "show me where employee is mentioned in HIPAA policy"
        - "find the section about employee access in ref 528"
        - "locate training requirements in verbal orders policy"
        """
        from app.services.instance_search_service import InstanceSearchService

        # Resolve policy name to reference number if needed
        resolved_policy = _resolve_policy_identifier(policy_id)
        logger.info(f"Instance search: term='{search_term}', policy='{policy_id}' -> resolved='{resolved_policy}'")

        # Create service and search
        service = InstanceSearchService(self.search_index.get_search_client())
        result = await asyncio.to_thread(
            service.search_within_policy,
            policy_ref=resolved_policy,
            query=search_term
        )

        # Handle no results
        if result.total_instances == 0:
            # Try to find if the policy exists at all
            policy_exists = len(service._get_policy_chunks(resolved_policy)) > 0

            if not policy_exists:
                response_text = (
                    f"I could not find a policy with reference '{policy_id}'. "
                    f"Please check the policy reference number and try again."
                )
            else:
                response_text = (
                    f"I searched **{result.policy_title}** (Ref #{resolved_policy}) "
                    f"but could not find any sections matching '{search_term}'.\n\n"
                    f"Try using different keywords or a more specific phrase."
                )

            return ChatResponse(
                response=response_text,
                summary=response_text,
                evidence=[],
                sources=[],
                found=False,
                confidence="high",
                chunks_used=0
            )

        # Format response with instance list
        response_parts = [
            f"Found **{result.total_instances} section(s)** matching '{search_term}' "
            f"in **{result.policy_title}** (Ref #{resolved_policy}):\n"
        ]

        # Show first 10 instances in chat
        for i, instance in enumerate(result.instances[:10], 1):
            page_info = f"Page {instance.page_number}" if instance.page_number else "N/A"
            section_info = ""
            if instance.section:
                section_info = f"Section {instance.section}"
                if instance.section_title:
                    section_info += f": {instance.section_title}"

            location = f"**{page_info}**"
            if section_info:
                location += f" - {section_info}"

            # Clean and truncate context
            context = instance.context.replace("\n", " ").strip()
            if len(context) > 150:
                context = context[:150] + "..."

            response_parts.append(f"\n{i}. {location}\n   _{context}_")

        if result.total_instances > 10:
            response_parts.append(f"\n\n_...and {result.total_instances - 10} more sections. Use the Search button on the policy card to explore all results._")

        response_text = "\n".join(response_parts)

        # Build evidence items from instances
        evidence_items = []
        for instance in result.instances[:5]:
            section_str = ""
            if instance.section:
                section_str = instance.section
                if instance.section_title:
                    section_str += f". {instance.section_title}"

            evidence_items.append(EvidenceItem(
                snippet=instance.context,
                citation=f"{result.policy_title} (Ref: {resolved_policy})",
                title=result.policy_title,
                reference_number=resolved_policy,
                section=section_str,
                page_number=instance.page_number,
                source_file=result.source_file,
                match_type="verified"
            ))

        sources = []
        if result.source_file:
            sources.append({
                "citation": f"{result.policy_title} (Ref: {resolved_policy})",
                "source_file": result.source_file,
                "title": result.policy_title,
                "reference_number": resolved_policy
            })

        return ChatResponse(
            response=response_text,
            summary=response_text,
            evidence=evidence_items,
            sources=sources,
            found=True,
            confidence="high",
            chunks_used=result.total_instances
        )

    async def process_chat(self, request: ChatRequest) -> ChatResponse:
        """
        Process a chat message using the best available search pipeline.

        Pipeline priority:
        0. Instance Search - "find X in policy Y" type queries (bypass normal RAG)
        1. Cohere Rerank (cross-encoder) - best for negation-aware queries
        2. Azure "On Your Data" (vectorSemanticHybrid) - good general quality
        3. Standard retrieval (fallback)
        """
        from app.core.config import settings

        # Priority 0: Detect "find X in policy Y" queries
        # These bypass normal RAG and use direct policy search
        instance_intent = _detect_instance_search_intent(request.message)
        if instance_intent:
            search_term, policy_id = instance_intent
            logger.info(f"Instance search query detected: '{search_term}' in policy '{policy_id}'")
            return await self._handle_instance_search(search_term, policy_id)

        # ===================================================================
        # EARLY QUERY VALIDATION (Before Cache Check)
        # These checks MUST run before cache lookup because:
        # 1. Cached responses may predate disambiguation logic
        # 2. Clarification requests should never be cached
        # ===================================================================

        # Unclear query detection (gibberish, single chars, vague)
        if self._is_unclear_query(request.message):
            logger.info(f"Unclear query detected: {request.message[:50]}...")
            return ChatResponse(
                response=UNCLEAR_QUERY_MESSAGE,
                summary=UNCLEAR_QUERY_MESSAGE,
                evidence=[],
                raw_response="",
                sources=[],
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["UNCLEAR_QUERY"]
            )

        # Out-of-scope detection (topics with no policies)
        if self._is_out_of_scope_query(request.message):
            logger.info(f"Out-of-scope query detected: {request.message[:50]}...")
            out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic is outside my scope."
            return ChatResponse(
                response=out_of_scope_msg,
                summary=out_of_scope_msg,
                evidence=[],
                raw_response="",
                sources=[],
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["OUT_OF_SCOPE"]
            )

        # Device ambiguity detection - Ask for clarification before searching
        # Must run BEFORE cache check to ensure disambiguation always triggers
        ambiguity_config = self.detect_device_ambiguity(request.message)
        if ambiguity_config:
            logger.info(f"Ambiguous device query detected: {request.message[:50]}...")
            return ChatResponse(
                response="",
                summary="",
                evidence=[],
                raw_response="",
                sources=[],
                chunks_used=0,
                found=False,
                confidence="clarification_needed",
                clarification=ambiguity_config
            )

        # Adversarial query detection (bypass/jailbreak attempts)
        if self._is_adversarial_query(request.message):
            logger.info(f"Adversarial query detected: {request.message[:50]}...")
            return ChatResponse(
                response=ADVERSARIAL_REFUSAL_MESSAGE,
                summary=ADVERSARIAL_REFUSAL_MESSAGE,
                evidence=[],
                raw_response="",
                sources=[],
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["ADVERSARIAL_BLOCKED"]
            )

        # Build safe filter expression
        filter_expr = build_applies_to_filter(request.filter_applies_to)

        # ===================================================================
        # RESPONSE CACHE CHECK (Cold Start Optimization)
        # Check if we have a cached response for this exact query + filter.
        # Cache hits return in <100ms instead of 4-6s.
        # ===================================================================
        if self.cache_service and self.cache_service.enabled:
            cached_response = self.cache_service.get_response(request.message, filter_expr)
            if cached_response is not None:
                logger.info(f"[CACHE HIT] Response cache hit for: {request.message[:50]}...")
                # Return cached response directly (already validated when cached)
                return cached_response

        # Priority 1: Cohere Rerank (cross-encoder for negation-aware search)
        # This pipeline: Azure Search → Cohere Rerank → Regular Chat Completions
        if (settings.USE_COHERE_RERANK and
            self.cohere_rerank_service and
            self.cohere_rerank_service.is_configured and
            self._openai_client):
            response = await self._chat_with_cohere_rerank(request, filter_expr)
            # Cache successful responses with evidence (skip error/not-found/clarification)
            if self.cache_service and self.cache_service.should_cache_response(response):
                self.cache_service.set_response(request.message, response, filter_expr)
            return response

        # Priority 2: Use On Your Data for full semantic hybrid search
        if self.on_your_data_service and self.on_your_data_service.is_configured:
            response = await self._chat_with_on_your_data(request, filter_expr)
            if self.cache_service and self.cache_service.should_cache_response(response):
                self.cache_service.set_response(request.message, response, filter_expr)
            return response

        # Fallback: Standard retrieval (search + basic response)
        response = await self._chat_with_standard_retrieval(request, filter_expr)
        if self.cache_service and self.cache_service.should_cache_response(response):
            self.cache_service.set_response(request.message, response, filter_expr)
        return response

    # =========================================================================
    # STREAMING CHAT RESPONSE (SSE)
    # =========================================================================

    async def process_chat_stream(self, request: ChatRequest):
        """
        Stream chat response using Server-Sent Events (SSE).

        Yields SSE events as strings in format: "event: <type>\ndata: <json>\n\n"

        Event types:
        - status: Pipeline progress updates
        - answer_chunk: Partial answer text
        - evidence: Evidence items array (sent once)
        - sources: Source references (sent once)
        - metadata: Response metadata
        - clarification: Device ambiguity clarification needed
        - done: End of stream marker
        - error: Error during streaming
        """
        import json
        from openai import RateLimitError, APITimeoutError

        def sse_event(event_type: str, data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        try:
            # Early validations (same as non-streaming)
            if self._is_unclear_query(request.message):
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": UNCLEAR_QUERY_MESSAGE})
                yield sse_event("metadata", {"type": "metadata", "confidence": "high", "found": False, "chunks_used": 0})
                yield sse_event("done", {"type": "done"})
                return

            if self._is_out_of_scope_query(request.message):
                out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic is outside my scope."
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": out_of_scope_msg})
                yield sse_event("metadata", {"type": "metadata", "confidence": "high", "found": False, "chunks_used": 0})
                yield sse_event("done", {"type": "done"})
                return

            # Device ambiguity detection
            ambiguity_config = self.detect_device_ambiguity(request.message)
            if ambiguity_config:
                yield sse_event("clarification", {
                    "type": "clarification",
                    "ambiguous_term": ambiguity_config.get("ambiguous_term", ""),
                    "message": ambiguity_config.get("message", ""),
                    "options": ambiguity_config.get("options", []),
                    "requires_clarification": True
                })
                yield sse_event("done", {"type": "done"})
                return

            if self._is_adversarial_query(request.message):
                adversarial_msg = "I'm a policy-only assistant and can't respond to requests that try to override my guidelines. How can I help with RUSH policies?"
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": adversarial_msg})
                yield sse_event("metadata", {"type": "metadata", "confidence": "high", "found": False, "chunks_used": 0, "safety_flags": ["ADVERSARIAL_BLOCKED"]})
                yield sse_event("done", {"type": "done"})
                return

            # Build filter expression
            filter_expr = build_applies_to_filter(request.filter_applies_to)

            # Status: Searching
            yield sse_event("status", {"type": "status", "message": "Searching policies..."})

            # Expand query
            expanded_query, expansion = self._expand_query(request.message)
            search_query, forced_refs = self._apply_policy_hints(expanded_query)

            # Search for documents
            retrieve_top_k = settings.COHERE_RETRIEVE_TOP_K

            # Check search cache
            search_results = None
            if self.cache_service and self.cache_service.enabled:
                cached_results = self.cache_service.get_search_results(
                    search_query, filter_expr, retrieve_top_k
                )
                if cached_results is not None:
                    search_results = cached_results

            if search_results is None:
                search_results = await asyncio.to_thread(
                    self.search_index.search,
                    search_query,
                    top=retrieve_top_k,
                    filter_expr=filter_expr,
                    use_semantic_ranking=True
                )
                if search_results and self.cache_service and self.cache_service.enabled:
                    self.cache_service.set_search_results(
                        search_query, search_results, filter_expr, retrieve_top_k
                    )

            if not search_results:
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": NOT_FOUND_MESSAGE})
                yield sse_event("metadata", {"type": "metadata", "confidence": "low", "found": False, "chunks_used": 0})
                yield sse_event("done", {"type": "done"})
                return

            # Status: Reranking
            yield sse_event("status", {"type": "status", "message": "Analyzing relevance..."})

            # Prepare docs for reranking
            docs_for_rerank = []
            for sr in search_results:
                docs_for_rerank.append({
                    "content": sr.content,
                    "title": sr.title,
                    "reference_number": sr.reference_number,
                    "source_file": sr.source_file,
                    "section": sr.section,
                    "applies_to": getattr(sr, 'applies_to', ''),
                    "page_number": getattr(sr, 'page_number', None)
                })

            # Rerank with Cohere
            if self.cohere_rerank_service and self.cohere_rerank_service.is_configured:
                dynamic_top_n = self._get_cohere_top_n(request.message)
                reranked = await self.cohere_rerank_service.rerank_async(
                    query=request.message,
                    documents=docs_for_rerank,
                    top_n=dynamic_top_n,
                    min_score=settings.COHERE_RERANK_MIN_SCORE
                )
            else:
                # Fallback: use search results as-is (limit to top 10)
                reranked = [
                    RerankResult(
                        content=doc.get('content', ''),
                        title=doc.get('title', ''),
                        reference_number=doc.get('reference_number', ''),
                        source_file=doc.get('source_file', ''),
                        section=doc.get('section', ''),
                        applies_to=doc.get('applies_to', ''),
                        page_number=doc.get('page_number'),
                        cohere_score=0.5,
                        original_index=i
                    ) for i, doc in enumerate(docs_for_rerank[:10])
                ]

            if not reranked:
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": NOT_FOUND_MESSAGE})
                yield sse_event("metadata", {"type": "metadata", "confidence": "low", "found": False, "chunks_used": 0})
                yield sse_event("done", {"type": "done"})
                return

            # Build context and evidence
            context_parts = []
            evidence_items = []
            seen_refs = set()

            for result in reranked:
                # RerankResult has direct attributes, not a document dict
                ref_num = result.reference_number or ''
                title = result.title or 'Unknown Policy'
                section = result.section or ''
                content = result.content or ''

                # Build context string
                context_header = f"[{title}"
                if ref_num:
                    context_header += f" (Ref #{ref_num})"
                context_header += "]"
                if section:
                    context_header += f" Section: {section}"
                context_parts.append(f"{context_header}\n{content}")

                # Build evidence item (dedupe by ref)
                if ref_num and ref_num not in seen_refs:
                    seen_refs.add(ref_num)
                    evidence_items.append({
                        "snippet": content[:500] if content else "",
                        "citation": f"[{ref_num}] {title}" if ref_num else title,
                        "title": title,
                        "reference_number": ref_num,
                        "section": section,
                        "applies_to": result.applies_to or '',
                        "source_file": result.source_file or '',
                        "page_number": result.page_number,
                        "match_type": "verified"
                    })

            context = "\n\n---\n\n".join(context_parts)

            # Status: Generating
            yield sse_event("status", {"type": "status", "message": "Generating answer..."})

            # Build messages for OpenAI
            messages = [
                {"role": "system", "content": RISEN_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {request.message}"}
            ]

            # Stream from OpenAI
            if self._openai_client:
                try:
                    stream = await asyncio.to_thread(
                        self._openai_client.chat.completions.create,
                        model=settings.AOAI_CHAT_DEPLOYMENT,
                        messages=messages,
                        temperature=0.0,
                        max_tokens=500,
                        stream=True
                    )

                    full_answer = ""
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            content = chunk.choices[0].delta.content
                            full_answer += content
                            yield sse_event("answer_chunk", {"type": "answer_chunk", "content": content})

                    # Check if response indicates not found
                    is_not_found = self._is_not_found_response(full_answer)
                    if is_not_found:
                        yield sse_event("evidence", {"type": "evidence", "items": []})
                        yield sse_event("sources", {"type": "sources", "items": []})
                        yield sse_event("metadata", {
                            "type": "metadata",
                            "confidence": "low",
                            "found": False,
                            "chunks_used": 0
                        })
                    else:
                        # Build sources from evidence
                        sources = []
                        for ev in evidence_items:
                            sources.append({
                                "citation": ev["citation"],
                                "source_file": ev.get("source_file", ""),
                                "title": ev["title"],
                                "reference_number": ev.get("reference_number"),
                                "section": ev.get("section"),
                                "applies_to": ev.get("applies_to"),
                                "match_type": ev.get("match_type", "verified")
                            })

                        yield sse_event("evidence", {"type": "evidence", "items": evidence_items})
                        yield sse_event("sources", {"type": "sources", "items": sources})

                        # Calculate confidence
                        avg_score = sum(r.cohere_score for r in reranked[:3]) / min(3, len(reranked))
                        confidence = "high" if avg_score > 0.7 else "medium" if avg_score > 0.4 else "low"

                        yield sse_event("metadata", {
                            "type": "metadata",
                            "confidence": confidence,
                            "confidence_score": round(avg_score, 3),
                            "found": True,
                            "chunks_used": len(reranked)
                        })

                except RateLimitError as e:
                    retry_after = 60
                    if hasattr(e, 'response') and e.response:
                        retry_after = int(e.response.headers.get('Retry-After', 60))
                    yield sse_event("error", {
                        "type": "error",
                        "message": "Too many requests. Please wait before trying again.",
                        "retry_after": retry_after
                    })
                    return
                except APITimeoutError:
                    yield sse_event("error", {
                        "type": "error",
                        "message": "Request timed out. Please try again.",
                        "retry_after": 5
                    })
                    return
            else:
                # No OpenAI client - return basic response
                yield sse_event("answer_chunk", {"type": "answer_chunk", "content": "LLM service is not configured."})
                yield sse_event("metadata", {"type": "metadata", "confidence": "low", "found": False, "chunks_used": 0})

            yield sse_event("done", {"type": "done"})

        except Exception as e:
            logger.error(f"Streaming chat failed: {e}", exc_info=True)
            yield sse_event("error", {
                "type": "error",
                "message": "An error occurred processing your request"
            })

    async def _chat_with_cohere_rerank(
        self,
        request: ChatRequest,
        filter_expr: Optional[str] = None
    ) -> ChatResponse:
        """
        Chat pipeline using Cohere cross-encoder reranking.

        Flow:
        1. Azure AI Search (vector + BM25) to get candidate documents
        2. Cohere Rerank (cross-encoder) to reorder by relevance + negation understanding
        3. Azure OpenAI Chat Completions with reranked context

        Why Cohere? Cross-encoders understand negation better than bi-encoders.
        "Can MA accept verbal orders?" - Cohere understands "NOT authorized" contradicts the query.
        """
        logger.info(f"Using Cohere Rerank pipeline for query: {request.message[:50]}...")

        # Early unclear query detection (gibberish, single chars, vague)
        if self._is_unclear_query(request.message):
            logger.info(f"Unclear query detected: {request.message[:50]}...")
            # NO references for clarification requests
            return ChatResponse(
                response=UNCLEAR_QUERY_MESSAGE,
                summary=UNCLEAR_QUERY_MESSAGE,
                evidence=[],  # NEVER include evidence for clarification
                raw_response="",
                sources=[],   # NEVER include sources for clarification
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["UNCLEAR_QUERY"]
            )

        # Early out-of-scope detection
        if self._is_out_of_scope_query(request.message):
            logger.info(f"Out-of-scope query detected: {request.message[:50]}...")
            # NO references for out-of-scope responses
            out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic is outside my scope."
            return ChatResponse(
                response=out_of_scope_msg,
                summary=out_of_scope_msg,
                evidence=[],  # NEVER include evidence for out-of-scope
                raw_response="",
                sources=[],   # NEVER include sources for out-of-scope
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["OUT_OF_SCOPE"]
            )

        # Device ambiguity detection - Ask for clarification before searching
        ambiguity_config = self.detect_device_ambiguity(request.message)
        if ambiguity_config:
            logger.info(f"Ambiguous device query detected: {request.message[:50]}...")
            # Return clarification request to frontend
            return ChatResponse(
                response="",
                summary="",
                evidence=[],
                raw_response="",
                sources=[],
                chunks_used=0,
                found=False,
                confidence="clarification_needed",
                clarification=ambiguity_config
            )

        # Adversarial query detection
        if self._is_adversarial_query(request.message):
            logger.info(f"Adversarial query detected: {request.message[:50]}...")
            # NO references for adversarial refusal responses
            return ChatResponse(
                response=ADVERSARIAL_REFUSAL_MESSAGE,
                summary=ADVERSARIAL_REFUSAL_MESSAGE,
                evidence=[],  # NEVER include evidence for refusals
                raw_response="",
                sources=[],   # NEVER include sources for refusals
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["ADVERSARIAL_BLOCKED"]
            )

        # Expand query with synonyms and domain-specific hints
        expanded_query, expansion = self._expand_query(request.message)
        search_query, forced_refs = self._apply_policy_hints(expanded_query)
        forced_ref_numbers = {entry.get("reference") for entry in forced_refs if entry.get("reference")}

        forced_doc_map: Dict[str, Dict[str, Any]] = {}

        try:
            # Step 1: Get candidate documents from Azure AI Search
            # Per industry best practices: retrieve 100+ docs for reranking
            # Research shows Cohere can move relevant docs from position 273 → 5
            retrieve_top_k = settings.COHERE_RETRIEVE_TOP_K  # Default: 100

            # Check search cache first (saves 1-2.5s on cache hit)
            search_results = None
            if self.cache_service and self.cache_service.enabled:
                cached_results = self.cache_service.get_search_results(
                    search_query, filter_expr, retrieve_top_k
                )
                if cached_results is not None:
                    logger.info(f"[CACHE HIT] Search cache hit: {len(cached_results)} results")
                    search_results = cached_results

            # Cache miss - execute search
            if search_results is None:
                search_results = await asyncio.to_thread(
                    self.search_index.search,
                    search_query,
                    top=retrieve_top_k,
                    filter_expr=filter_expr,
                    use_semantic_ranking=True
                )
                # Cache the results for future queries
                if search_results and self.cache_service and self.cache_service.enabled:
                    self.cache_service.set_search_results(
                        search_query, search_results, filter_expr, retrieve_top_k
                    )

            logger.info(f"Retrieved {len(search_results) if search_results else 0} candidates for Cohere reranking")

            if not search_results:
                logger.warning("No search results returned")
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["NO_SEARCH_RESULTS"]
                )

            # Convert SearchResults to dicts for Cohere
            docs_for_rerank = []
            for sr in search_results:
                record = {
                    "content": sr.content,
                    "title": sr.title,
                    "reference_number": sr.reference_number,
                    "source_file": sr.source_file,
                    "section": sr.section,
                    "applies_to": getattr(sr, 'applies_to', ''),
                    "page_number": getattr(sr, 'page_number', None)
                }
                docs_for_rerank.append(record)
                if sr.reference_number in forced_ref_numbers and sr.reference_number not in forced_doc_map:
                    forced_doc_map[sr.reference_number] = record
            original_docs = list(docs_for_rerank)

            if forced_refs:
                existing_refs = {doc.get("reference_number") for doc in docs_for_rerank}
                for entry in forced_refs:
                    ref = entry.get("reference")
                    policy_query = entry.get("policy_query") or entry.get("hint")
                    if not ref or ref in existing_refs:
                        continue
                    try:
                        targeted = await asyncio.to_thread(
                            self.search_index.search,
                            f"{policy_query} {request.message}",
                            top=3,
                            filter_expr=filter_expr,
                            use_semantic_ranking=True,
                            use_fuzzy=False
                        )
                        for sr in targeted:
                            if sr.reference_number and sr.reference_number != ref:
                                continue
                            if sr.reference_number and sr.reference_number not in existing_refs:
                                record = {
                                    "content": sr.content,
                                    "title": sr.title,
                                    "reference_number": sr.reference_number,
                                    "source_file": sr.source_file,
                                    "section": sr.section,
                                    "applies_to": getattr(sr, 'applies_to', ''),
                                    "page_number": getattr(sr, 'page_number', None)
                                }
                                docs_for_rerank.append(record)
                                forced_doc_map.setdefault(ref, record)
                                existing_refs.add(sr.reference_number)
                                break
                    except Exception as e:
                        logger.warning(f"Forced reference lookup failed for Ref #{ref}: {e}")
                original_docs = list(docs_for_rerank)

            # CORRECTIVE RAG: Evaluate retrieval quality BEFORE generation
            # This catches low-quality retrievals that could lead to hallucinations
            try:
                crag_service = get_corrective_rag_service()
                quality_assessments = crag_service.assess_retrieval_quality(
                    query=request.message,
                    documents=docs_for_rerank
                )
                corrective_action = crag_service.determine_corrective_action(
                    query=request.message,
                    assessments=quality_assessments
                )
                
                if corrective_action.action == "refuse":
                    logger.warning("cRAG: insufficient quality; proceeding with unfiltered document set")
                    docs_for_rerank = original_docs[: settings.COHERE_RETRIEVE_TOP_K]
                else:
                    filtered_docs = crag_service.filter_documents_by_quality(
                        docs_for_rerank, quality_assessments, corrective_action
                    )
                    if filtered_docs:
                        docs_for_rerank = filtered_docs
                        logger.info(f"cRAG: Filtered to {len(docs_for_rerank)} quality-approved docs")
                    elif corrective_action.relevant_docs:
                        docs_for_rerank = [
                            original_docs[i]
                            for i in corrective_action.relevant_docs
                            if i < len(original_docs)
                        ]
                        logger.info("cRAG: using relevant doc indices despite low aggregate score")
                
                if not docs_for_rerank:
                    logger.info("cRAG filtering produced no docs; reverting to original candidate set")
                    docs_for_rerank = original_docs
                
            except Exception as e:
                logger.warning(f"Corrective RAG check failed (non-critical): {e}")

            if forced_ref_numbers:
                existing_refs = {doc.get("reference_number") for doc in docs_for_rerank}
                for ref in forced_ref_numbers:
                    if ref and ref not in existing_refs:
                        for candidate in original_docs:
                            if candidate.get("reference_number") == ref:
                                docs_for_rerank.append(candidate)
                                existing_refs.add(ref)
                                logger.info(f"Forced inclusion of Ref #{ref} to maintain policy coverage")
                                break

            if not docs_for_rerank:
                logger.warning("No documents available for reranking after cRAG processing")
                if self.on_your_data_service and self.on_your_data_service.is_configured:
                    return await self._chat_with_on_your_data(request, filter_expr)
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["CRAG_NO_DOCS"]
                )

            # Step 2: Cohere rerank the documents
            # Use dynamic top_n based on query complexity
            dynamic_top_n = self._get_cohere_top_n(request.message)
            reranked = await self.cohere_rerank_service.rerank_async(
                query=request.message,  # Use original query for reranking
                documents=docs_for_rerank,
                top_n=dynamic_top_n,
                min_score=settings.COHERE_RERANK_MIN_SCORE  # Explicit threshold
            )
            logger.info(f"Cohere reranked {len(docs_for_rerank)} docs → top {dynamic_top_n} results")

            # FIX: Check if forced refs are missing from reranked results (sparse retrieval fallback)
            # This handles cases like "unit secretary" where the canonical policy doesn't score well
            if forced_ref_numbers and reranked:
                reranked_refs = {rr.reference_number for rr in reranked if rr.reference_number}
                missing_forced = forced_ref_numbers - reranked_refs
                if missing_forced:
                    logger.warning(f"Forced refs {missing_forced} not in reranked results; retrying with min_score=0.05")
                    reranked_with_lower_threshold = await self.cohere_rerank_service.rerank_async(
                        query=request.message,
                        documents=docs_for_rerank,
                        top_n=dynamic_top_n * 2,  # Expand result set
                        min_score=0.05  # Lower threshold for sparse queries
                    )
                    if reranked_with_lower_threshold:
                        # Check if retry found the missing forced refs
                        retry_refs = {rr.reference_number for rr in reranked_with_lower_threshold if rr.reference_number}
                        found_forced = missing_forced & retry_refs
                        if found_forced:
                            logger.info(f"Sparse fallback found forced refs: {found_forced}")
                            reranked = reranked_with_lower_threshold

            # NEW: Apply score windowing for single-intent queries
            # This filters out noise from related-but-different policies
            if reranked and len(reranked) > 3:
                reranked = self.filter_by_score_window(
                    reranked,
                    request.message,
                    window_threshold=0.6  # Keep docs with score >= 60% of top score
                )

            if not reranked:
                logger.warning("Cohere rerank returned no results at calibrated threshold; retrying with min_score=0.0")
                reranked = await self.cohere_rerank_service.rerank_async(
                    query=request.message,
                    documents=docs_for_rerank,
                    top_n=dynamic_top_n,
                    min_score=0.0
                )

            if not reranked:
                logger.warning("Cohere rerank still empty after relaxed threshold")
                if self.on_your_data_service and self.on_your_data_service.is_configured:
                    logger.info("Falling back to On Your Data due to empty rerank set")
                    return await self._chat_with_on_your_data(request, filter_expr)
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["NO_RERANK_RESULTS"]
                )

            if forced_ref_numbers:
                # FIX: Boost scores of forced refs that ARE in results but ranked low
                # This ensures canonical policies like Ref #486 rank in top 3 for verbal order queries
                boosted_reranked = []
                for rr in reranked:
                    if rr.reference_number in forced_ref_numbers:
                        # Boost score to ensure canonical policy ranks high (1.5x, min 0.5)
                        boosted_score = max(rr.cohere_score * 1.5, 0.5)
                        boosted_reranked.append(RerankResult(
                            content=rr.content,
                            title=rr.title,
                            reference_number=rr.reference_number,
                            source_file=rr.source_file,
                            section=rr.section,
                            applies_to=rr.applies_to,
                            page_number=rr.page_number,
                            cohere_score=boosted_score,
                            original_index=rr.original_index
                        ))
                        logger.info(f"Boosted forced ref #{rr.reference_number}: {rr.cohere_score:.3f} → {boosted_score:.3f}")
                    else:
                        boosted_reranked.append(rr)

                # Re-sort by boosted scores
                reranked = sorted(boosted_reranked, key=lambda r: r.cohere_score, reverse=True)

                # Append any missing forced refs (existing logic)
                reranked_refs = {rr.reference_number for rr in reranked if rr.reference_number}
                for ref in forced_ref_numbers:
                    if not ref or ref in reranked_refs or ref not in forced_doc_map:
                        continue
                    doc = forced_doc_map[ref]
                    reranked.append(RerankResult(
                        content=doc.get("content", ""),
                        title=doc.get("title", ""),
                        reference_number=ref,
                        source_file=doc.get("source_file", ""),
                        section=doc.get("section", ""),
                        applies_to=doc.get("applies_to", ""),
                        cohere_score=0.35,
                        original_index=len(reranked)
                    ))
                    reranked_refs.add(ref)
                    logger.info(f"Appended forced rerank result for Ref #{ref}")
            
            # Apply surge capacity penalty to deprioritize rarely-used surge policies
            # This prevents surge policies from appearing at top for general queries
            reranked = _apply_surge_capacity_penalty(
                reranked,
                penalty=settings.SURGE_CAPACITY_PENALTY
            )

            # Apply population-based ranking (pediatric vs adult default)
            # DEFAULT: Boost adult/general policies (most clinical queries are for adults)
            # PEDIATRIC: Boost pediatric policies when user mentions kids/peds/NICU/etc.
            is_pediatric_query = _detect_pediatric_context(request.message)
            reranked = _apply_population_ranking(
                reranked,
                is_pediatric_query=is_pediatric_query,
                pediatric_boost=1.3,    # 30% boost for peds policies when peds query
                adult_default_boost=1.2  # 20% boost for adult/general when no peds keywords
            )

            # Apply location boost for entity-specific queries
            # This prioritizes policies matching mentioned RUSH entities (RUMC, ROPH, etc.)
            query_entities = _extract_entity_mentions(request.message)
            if query_entities:
                reranked = _apply_location_boost(
                    reranked,
                    query_entities,
                    boost=settings.LOCATION_MATCH_BOOST
                )

            # Apply MMR diversification for multi-policy queries
            # This ensures results come from different policies, not just different chunks
            is_multi_policy = self._is_multi_policy_query(request.message)
            if is_multi_policy and len(reranked) > 3:
                reranked = _apply_mmr_to_rerank_results(
                    reranked,
                    lambda_param=0.6,  # 60% relevance, 40% diversity
                    max_results=10
                )
                logger.info(f"Applied MMR diversification for multi-policy query: {len(reranked)} diverse results")

            # Step 3: Build context from reranked results
            context_parts = []
            evidence_items = []
            sources = []
            seen_refs = set()

            for rr in reranked:
                title = _normalize_policy_title(rr.title)
                # Build context string
                context_parts.append(
                    f"[{title} (Ref #{rr.reference_number})] "
                    f"Section: {rr.section or 'N/A'}\n{rr.content}"
                )

                # Build evidence items (deduplicated by ref)
                if rr.reference_number not in seen_refs:
                    evidence_items.append(EvidenceItem(
                        snippet=_truncate_verbatim(rr.content),
                        citation=f"{title} (Ref #{rr.reference_number})" if rr.reference_number else title,
                        title=title,
                        reference_number=rr.reference_number,
                        section=rr.section,
                        applies_to=rr.applies_to,
                        source_file=rr.source_file,
                        page_number=rr.page_number,
                        reranker_score=rr.cohere_score,
                        match_type="verified",
                    ))
                    sources.append({
                        "title": title,
                        "reference_number": rr.reference_number,
                        "section": rr.section,
                        "source_file": rr.source_file,
                        "cohere_score": rr.cohere_score
                    })
                    seen_refs.add(rr.reference_number)

            context = "\n\n---\n\n".join(context_parts)

            if forced_ref_numbers:
                ordered_evidence = []
                ordered_sources = []
                used_indices = set()
                forced_order = [entry.get("reference") for entry in forced_refs if entry.get("reference")]
                for ref in forced_order:
                    for idx, item in enumerate(evidence_items):
                        if idx in used_indices:
                            continue
                        if item.reference_number == ref:
                            ordered_evidence.append(item)
                            ordered_sources.append(sources[idx])
                            used_indices.add(idx)
                            break
                for idx, item in enumerate(evidence_items):
                    if idx not in used_indices:
                        ordered_evidence.append(item)
                        ordered_sources.append(sources[idx])
                evidence_items = ordered_evidence
                sources = ordered_sources

            # Step 4: Call Azure OpenAI Chat Completions with RISEN prompt
            messages = [
                {"role": "system", "content": RISEN_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {request.message}"}
            ]

            # Dynamic max_tokens: multi-policy queries need more space for comprehensive answers
            max_tokens = 800 if is_multi_policy else 500

            response = await asyncio.to_thread(
                self._openai_client.chat.completions.create,
                model=settings.AOAI_CHAT_DEPLOYMENT,
                messages=messages,
                temperature=0.0,  # HEALTHCARE: Zero temperature for deterministic, factual responses
                max_tokens=max_tokens
            )

            answer_text = response.choices[0].message.content or NOT_FOUND_MESSAGE

            # CRITICAL: Strip any references from negative response types
            # The LLM might include refs even when saying "I could not find"
            answer_text = _strip_references_from_negative_response(answer_text)

            # Check for NOT_FOUND patterns
            if self._is_not_found_response(answer_text):
                # But if we have evidence, trust the response
                if evidence_items:
                    logger.info(f"NOT_FOUND override: {len(evidence_items)} evidence items exist")
                else:
                    # Return clean NOT_FOUND with NO references
                    return ChatResponse(
                        response=NOT_FOUND_MESSAGE,
                        summary=NOT_FOUND_MESSAGE,
                        evidence=[],  # NEVER include evidence for not-found
                        raw_response=answer_text,
                        sources=[],   # NEVER include sources for not-found
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        safety_flags=["LLM_NOT_FOUND"]
                    )

            # CRITICAL FIX: Check for refusal/out-of-scope responses
            # Even if evidence was retrieved (e.g., keyword matches for "Chicago"),
            # if the LLM says "I only answer RUSH policy questions", clear all citations
            if _is_refusal_response(answer_text):
                logger.info(f"Refusal response detected, clearing {len(evidence_items)} false positive citations")
                # Return refusal with NO references (even if search found keyword matches)
                return ChatResponse(
                    response=answer_text,
                    summary=answer_text,
                    evidence=[],  # NEVER include evidence for refusals
                    raw_response=answer_text,
                    sources=[],   # NEVER include sources for refusals
                    chunks_used=0,
                    found=False,
                    confidence="high",  # High confidence in the refusal
                    safety_flags=["LLM_REFUSAL"]
                )

            # Calculate confidence from Cohere rerank scores
            confidence_score, confidence_level = self._calculate_response_confidence(
                reranked, has_evidence=bool(evidence_items)
            )
            
            # Prepare contexts for validation (handle empty case)
            contexts = [rr.content for rr in reranked] if reranked else []
            
            # Citation verification - detect hallucinated references
            try:
                citation_verifier = get_citation_verifier()
                verification = citation_verifier.verify_response(
                    response=answer_text,
                    contexts=contexts,
                    sources=sources
                )
                
                # Add citation verification flags
                citation_flags = verification.flags if verification.flags else []
                if verification.hallucination_risk > 0.3:
                    citation_flags.append(f"HALLUCINATION_RISK:{verification.hallucination_risk:.2f}")
                    logger.warning(
                        f"Citation verification: hallucination_risk={verification.hallucination_risk:.2f}, "
                        f"flags={verification.flags}"
                    )
                
                # HEALTHCARE CRITICAL: Verify all factual claims (numbers, dosages, timeframes)
                # Multi-policy queries get slightly relaxed verification (claims can be in ANY context)
                facts_verified, unverified_facts, fact_flags = citation_verifier.verify_factual_claims(
                    response=answer_text,
                    contexts=contexts,
                    is_multi_policy=is_multi_policy
                )
                citation_flags.extend(fact_flags)
                
                if not facts_verified and unverified_facts:
                    logger.warning(f"HEALTHCARE SAFETY: Blocking response with unverified facts: {unverified_facts}")
                    return ChatResponse(
                        response="I could not verify all factual claims against RUSH policy documents. "
                                 f"Please check {settings.POLICYTECH_URL} or contact Policy Administration.",
                        summary="Unable to verify factual accuracy",
                        evidence=[],
                        raw_response=answer_text,
                        sources=[],
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        confidence_score=confidence_score,
                        needs_human_review=True,
                        safety_flags=citation_flags + ["BLOCKED_UNVERIFIED_FACTS"]
                    )
                
                # HEALTHCARE CRITICAL: Verify no fabricated policy references
                refs_verified, fabricated_refs, ref_flags = citation_verifier.verify_no_fabricated_refs(
                    response=answer_text,
                    context_refs=verification.context_refs if verification else set()
                )
                citation_flags.extend(ref_flags)
                
                if not refs_verified and fabricated_refs:
                    logger.warning(f"HEALTHCARE SAFETY: Blocking response with fabricated refs: {fabricated_refs}")
                    return ChatResponse(
                        response="I could not verify all policy citations. "
                                 f"Please check {settings.POLICYTECH_URL} for accurate policy information.",
                        summary="Unable to verify policy citations",
                        evidence=[],
                        raw_response=answer_text,
                        sources=[],
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        confidence_score=confidence_score,
                        needs_human_review=True,
                        safety_flags=citation_flags + ["BLOCKED_FABRICATED_REFS"]
                    )
                
            except Exception as e:
                logger.warning(f"Citation verification failed (non-critical): {e}")
                citation_flags = []
                verification = None
            
            confidence_score = self._boost_confidence_with_grounding(
                confidence_score,
                evidence_items,
                verification
            )
            confidence_level = self._confidence_level_from_score(confidence_score)

            # CRITICAL: Format answer with citations BEFORE safety validation
            # This ensures the safety validator sees the response with properly formatted citations
            # Without this, NO_CITATION flag triggers even when we have evidence (citations added later)
            formatted_answer = _format_answer_with_citations(answer_text, evidence_items)

            # Safety validation for healthcare
            try:
                safety_validator = get_safety_validator(strict_mode=True)
                safety_result = safety_validator.validate(
                    response_text=formatted_answer,  # Use formatted answer with citations
                    contexts=contexts,
                    confidence_score=confidence_score,
                    has_evidence=bool(evidence_items)
                )
                
                # Combine citation and safety flags
                all_flags = list(set(safety_result.flags + citation_flags))
                
            except Exception as e:
                logger.warning(f"Safety validation failed (non-critical): {e}")
                # Graceful degradation - allow response but flag for review
                safety_result = None
                all_flags = citation_flags + ["SAFETY_CHECK_SKIPPED"]
            
            # HEALTHCARE SAFETY: ALWAYS block responses that fail safety validation
            # Patient safety requires blocking, not just flagging, unsafe responses
            if safety_result and not safety_result.safe:
                logger.warning(f"HEALTHCARE SAFETY BLOCK: {all_flags}")
                fallback = (
                    safety_result.fallback_response or
                    f"I could not verify this information against RUSH policies. "
                    f"Please check {settings.POLICYTECH_URL} or contact Policy Administration."
                )
                return ChatResponse(
                    response=fallback,
                    summary=fallback,
                    evidence=[],
                    raw_response=answer_text,
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    confidence_score=confidence_score,
                    needs_human_review=True,
                    safety_flags=all_flags + ["BLOCKED_BY_SAFETY_CHECK"]
                )
            
            # HEALTHCARE SAFETY: Block if citation verification found HIGH hallucination risk
            # Note: 0.5 threshold allows responses without inline citations if content is grounded
            # A response with good content but no "Ref #XXX" citations scores ~0.4 (not a hallucination)
            if verification and verification.hallucination_risk > 0.5:
                logger.warning(f"HEALTHCARE SAFETY BLOCK: Hallucination risk {verification.hallucination_risk:.2f}")
                return ChatResponse(
                    response="I could not verify all claims in this response against RUSH policies. "
                             f"Please check {settings.POLICYTECH_URL} or contact Policy Administration.",
                    summary="Unable to verify response accuracy",
                    evidence=[],
                    raw_response=answer_text,
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    confidence_score=confidence_score,
                    needs_human_review=True,
                    safety_flags=all_flags + ["BLOCKED_HALLUCINATION_RISK"]
                )
            
            # Determine if human review needed
            needs_review = (
                (safety_result and safety_result.needs_human_review) or
                (verification and verification.hallucination_risk > 0.5) or
                confidence_level == "low"
            )
            
            # SELF-REFLECTIVE RAG: Critique response for grounding before returning
            # This catches issues that slipped past safety validation
            try:
                self_reflective_service = get_self_reflective_service()
                critique = self_reflective_service.critique_response(
                    response=answer_text,
                    query=request.message,
                    contexts=contexts
                )
                
                if not critique.overall_pass:
                    logger.warning(f"Self-Reflective critique failed: {critique.issues}")
                    # Add flags but don't block - critique is advisory
                    all_flags.append("SELF_CRITIQUE_WARNING")
                    if not critique.is_grounded:
                        all_flags.append("LOW_GROUNDING")
                    if critique.unsupported_claims:
                        all_flags.append("UNSUPPORTED_CLAIMS")
                    # Trigger human review for low-confidence critiques
                    if critique.confidence < 0.5:
                        needs_review = True
                else:
                    logger.debug(f"Self-Reflective critique passed: confidence={critique.confidence:.2f}")
                    
            except Exception as e:
                logger.warning(f"Self-Reflective critique failed (non-critical): {e}")
            
            # Dynamic evidence limit: multi-policy queries return more citations
            max_evidence = 10 if is_multi_policy else 5
            evidence_payload = evidence_items[:max_evidence]
            sources_payload = sources[:max_evidence]

            formatter = get_citation_formatter()
            formatted = formatter.format(
                answer_text=answer_text,
                evidence=evidence_payload,
                max_refs=max_evidence,
                found=True,
            )

            summary_text = formatted.summary or (
                answer_text[:200] + "..." if len(answer_text) > 200 else answer_text
            )
            response_text = formatted.response or answer_text

            return ChatResponse(
                response=response_text,
                summary=summary_text,
                evidence=evidence_payload,
                raw_response=answer_text,
                sources=sources_payload,
                chunks_used=len(reranked),
                found=True,
                confidence=confidence_level,
                confidence_score=confidence_score,
                needs_human_review=needs_review,
                safety_flags=all_flags,
                search_query=search_query  # Expanded query for audit/synonym analysis
            )

        except Exception as e:
            logger.error(f"Cohere rerank pipeline failed: {e}")
            # Fallback to On Your Data if available
            if self.on_your_data_service and self.on_your_data_service.is_configured:
                logger.info("Falling back to On Your Data pipeline")
                return await self._chat_with_on_your_data(request, filter_expr)
            raise

    def _extract_policy_refs_from_response(self, response_text: str) -> List[dict]:
        """
        Extract policy references mentioned in the agent's response.

        The agent uses various citation formats:
        - [Policy Name, Ref #XXXX]
        - "Policy Name" policy [Ref #XXXX]
        - Policy: Policy Name with Reference Number: XXXX
        - [Ref #XXXX] standalone

        Returns list of dicts with 'title' and 'reference_number' keys.
        """
        import re
        refs = []

        # Pattern 1: [Title, Ref #XXXX] - title and ref in same bracket
        pattern1 = r'\[([^,\]]+?)(?:,\s*Ref\s*[#:]?\s*|,\s*Reference\s*(?:Number)?[:#]?\s*)([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern1, response_text, re.IGNORECASE):
            refs.append({'title': match.group(1).strip(), 'reference_number': match.group(2).strip()})

        # Pattern 2: "Title" policy [Ref #XXXX] - quoted title before ref bracket
        pattern2 = r'"([^"]+)"\s*(?:policy)?\s*\[Ref\s*[#:]?\s*([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern2, response_text, re.IGNORECASE):
            refs.append({'title': match.group(1).strip(), 'reference_number': match.group(2).strip()})

        # Pattern 3: Policy: Title Name (in formatted box) + Reference Number: XXXX
        policy_title_match = re.search(r'Policy:\s*([^\n│]+)', response_text)
        ref_num_match = re.search(r'Reference\s*Number[:#]?\s*([A-Z0-9\.\-]{2,15})', response_text, re.IGNORECASE)
        if policy_title_match and ref_num_match:
            title = policy_title_match.group(1).strip().rstrip('│').strip()
            ref_num = ref_num_match.group(1).strip()
            refs.append({'title': title, 'reference_number': ref_num})

        # Pattern 4: [Ref #XXXX] standalone - try to find nearby title
        pattern4 = r'\[Ref\s*[#:]?\s*([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern4, response_text, re.IGNORECASE):
            ref_num = match.group(1).strip()
            # Check if we already have this ref
            if any(r['reference_number'] == ref_num for r in refs):
                continue
            # Try to find a quoted title before this reference
            before_text = response_text[:match.start()]
            title_before = re.search(r'"([^"]+)"\s*(?:policy)?\s*$', before_text)
            if title_before:
                refs.append({'title': title_before.group(1).strip(), 'reference_number': ref_num})
            else:
                refs.append({'title': '', 'reference_number': ref_num})

        # Deduplicate by reference number, preferring entries with titles
        seen = {}
        for ref in refs:
            ref_num = ref['reference_number']
            if ref_num:
                if ref_num not in seen or (ref['title'] and not seen[ref_num]['title']):
                    seen[ref_num] = ref

        return list(seen.values())

    async def _chat_with_on_your_data(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """
        Handle chat using Azure OpenAI "On Your Data" with vectorSemanticHybrid.

        This provides the BEST search quality:
        - Vector similarity (text-embedding-3-large)
        - BM25 keyword matching
        - L2 semantic reranking (the key feature!)

        The citations come directly from Azure AI Search via the On Your Data API,
        ensuring accurate source attribution.
        """
        logger.info(f"Using On Your Data (vectorSemanticHybrid) for query: {request.message[:50]}...")

        # Early unclear query detection (gibberish, single chars, vague)
        if self._is_unclear_query(request.message):
            logger.info(f"Unclear query detected: {request.message[:50]}...")
            # NO references for clarification requests
            return ChatResponse(
                response=UNCLEAR_QUERY_MESSAGE,
                summary=UNCLEAR_QUERY_MESSAGE,
                evidence=[],  # NEVER include evidence for clarification
                raw_response="",
                sources=[],   # NEVER include sources for clarification
                chunks_used=0,
                found=False,
                safety_flags=["UNCLEAR_QUERY"]
            )

        # FIX 2: Early out-of-scope detection (before any API calls)
        if self._is_out_of_scope_query(request.message):
            logger.info(f"Out-of-scope query detected: {request.message[:50]}...")
            out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic (parking, HR benefits, administrative matters) is outside my scope. Please contact Human Resources or the appropriate department."
            # NO references for out-of-scope responses
            return ChatResponse(
                response=out_of_scope_msg,
                summary=out_of_scope_msg,
                evidence=[],  # NEVER include evidence for out-of-scope
                raw_response="",
                sources=[],   # NEVER include sources for out-of-scope
                chunks_used=0,
                found=False,
                safety_flags=["OUT_OF_SCOPE"]
            )

        # FIX 6: Adversarial query detection (bypass/circumvent safety protocols)
        if self._is_adversarial_query(request.message):
            logger.info(f"Adversarial query detected: {request.message[:50]}...")
            # NO references for refusal responses
            return ChatResponse(
                response=ADVERSARIAL_REFUSAL_MESSAGE,
                summary=ADVERSARIAL_REFUSAL_MESSAGE,
                evidence=[],  # NEVER include evidence for refusals
                raw_response="",
                sources=[],   # NEVER include sources for refusals
                chunks_used=0,
                found=False,
                safety_flags=["ADVERSARIAL_BLOCKED"]
            )

        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        # P0: HyDE is disabled - testing showed it causes regressions with Azure "On Your Data"
        # The On Your Data API does its own query expansion, so HyDE interferes
        # Keep the method for future experimentation but don't use it by default
        # TODO: Re-enable HyDE only when using direct search (not On Your Data)

        # FIX 7: Get dynamic search parameters based on query type
        search_params = self._get_search_params(request.message)
        logger.info(
            f"Search params for query: strictness={search_params['strictness']}, "
            f"top_n={search_params['top_n_documents']}"
        )

        try:
            # 60s timeout allows for: embedding (1-2s) + search (1-3s) + generation (5-10s)
            # + retry backoff (up to 14s for 3 retries with exponential backoff) + buffer
            result: OnYourDataResult = await asyncio.wait_for(
                self.on_your_data_service.chat(
                    query=expanded_query,
                    filter_expr=filter_expr,
                    top_n_documents=search_params['top_n_documents'],
                    strictness=search_params['strictness']
                ),
                timeout=60.0
            )

            answer_text = result.answer or NOT_FOUND_MESSAGE

            # CRITICAL: Strip any references from negative response types
            # The LLM might include refs even when saying "I could not find"
            answer_text = _strip_references_from_negative_response(answer_text)

            # FIX 1: Use expanded "not found" detection
            # FIX 8: Context-aware NOT_FOUND - if citations exist, trust the response
            # This prevents false positives when LLM says "could not find specific X"
            # but actually DID retrieve relevant documents
            has_citations = bool(result.citations and len(result.citations) > 0)

            # Phase 2 Diagnostic: Log which phrases trigger NOT_FOUND
            if self._is_not_found_response(answer_text):
                logger.warning(f"NOT_FOUND triggered for query: '{request.message[:80]}...'")
                logger.warning(f"LLM response that triggered: '{answer_text[:300]}...'")
                # Log which specific phrase matched for diagnosis
                matched_phrase = None
                answer_lower = answer_text.lower()
                for phrase in NOT_FOUND_PHRASES:
                    if phrase in answer_lower:
                        matched_phrase = phrase
                        logger.warning(f"Matched NOT_FOUND phrase: '{phrase}'")
                        break
                if not matched_phrase and answer_text == NOT_FOUND_MESSAGE:
                    logger.warning("Matched: exact NOT_FOUND_MESSAGE constant")
                elif not matched_phrase:
                    logger.warning("NOT_FOUND triggered but no phrase matched (empty response?)")

                # FIX 8: If there ARE citations, don't treat as "not found"
                # The LLM may say "could not find X" but still provide useful info
                if has_citations:
                    logger.info(
                        f"NOT_FOUND override: {len(result.citations)} citations exist, "
                        f"treating as valid response despite phrase match"
                    )
                else:
                    # Return clean NOT_FOUND with NO references
                    return ChatResponse(
                        response=NOT_FOUND_MESSAGE,
                        summary=NOT_FOUND_MESSAGE,
                        evidence=[],  # NEVER include evidence for not-found
                        raw_response=str(result.raw_response),
                        sources=[],   # NEVER include sources for not-found
                        chunks_used=0,
                        found=False
                    )

            # CRITICAL FIX: Check for refusal/out-of-scope responses
            # Even if citations exist (e.g., keyword matches for "Chicago"),
            # if the LLM says "I only answer RUSH policy questions", clear all citations
            if _is_refusal_response(answer_text):
                logger.info(f"Refusal response detected, clearing {len(result.citations) if result.citations else 0} false positive citations")
                return ChatResponse(
                    response=answer_text,
                    summary=answer_text,
                    evidence=[],  # NEVER include evidence for refusals
                    raw_response=str(result.raw_response),
                    sources=[],   # NEVER include sources for refusals
                    chunks_used=0,
                    found=False,
                    confidence="high",  # High confidence in the refusal
                    safety_flags=["LLM_REFUSAL"]
                )

            # If we reach here, we have a valid answer (not an early "not found" return)
            found = True

            # Convert On Your Data citations to EvidenceItems
            # Enrich citations with metadata from Azure AI Search
            evidence_items = []
            sources = []

            # FIX 5: Dynamic citation limit for multi-policy queries
            is_multi_policy = self._is_multi_policy_query(request.message)
            max_citations = 10 if is_multi_policy else 5

            # FIX 8: Apply MMR diversification for multi-policy queries
            # This ensures citations come from different policies, not just different chunks
            citations_to_process = result.citations
            if is_multi_policy and len(result.citations) > max_citations:
                citations_to_process = _apply_mmr_diversification(
                    result.citations,
                    lambda_param=0.6,  # 60% relevance, 40% diversity for multi-policy
                    max_results=max_citations
                )
                logger.info(f"Applied MMR diversification: {len(result.citations)} -> {len(citations_to_process)} citations")

            for cit in citations_to_process[:max_citations]:
                source_file = cit.filepath or ""

                # Look up full metadata from Azure AI Search by source_file
                metadata = None
                if source_file:
                    try:
                        metadata = await asyncio.to_thread(
                            self.search_index.get_metadata_by_source_file,
                            source_file
                        )
                    except Exception as e:
                        logger.warning(f"Failed to get metadata for {source_file}: {e}")

                # Use metadata from lookup, falling back to citation data
                ref_num = ""
                applies_to = ""
                section = ""
                date_updated = ""
                title = _normalize_policy_title(cit.title)

                if metadata:
                    ref_num = metadata.get("reference_number", "")
                    applies_to = metadata.get("applies_to", "")
                    section = metadata.get("section", "") or cit.section
                    date_updated = metadata.get("date_updated", "")
                    title = _normalize_policy_title(metadata.get("title", "") or cit.title)
                    logger.debug(f"Enriched citation {source_file}: applies_to={applies_to}")
                else:
                    # Fallback: Try to extract ref number from filepath (e.g., "hr-001.pdf" -> "HR-001")
                    if source_file:
                        import re
                        ref_match = re.search(r'([a-z]{2,4}[-_]?\d{2,4})', source_file.lower())
                        if ref_match:
                            ref_num = ref_match.group(1).upper().replace('_', '-')

                evidence_items.append(
                    EvidenceItem(
                        snippet=_truncate_verbatim(cit.content),
                        citation=f"{title} ({ref_num})" if ref_num else title,
                        title=title,
                        reference_number=ref_num,
                        section=section,
                        applies_to=applies_to,
                        date_updated=date_updated,
                        source_file=source_file,
                        page_number=cit.page_number,
                        score=None,
                        reranker_score=cit.reranker_score,
                        match_type="verified",  # Citations come directly from search
                    )
                )

                sources.append({
                    "citation": f"{title} ({ref_num})" if ref_num else title,
                    "source_file": source_file,
                    "title": title,
                    "reference_number": ref_num,
                    "section": section,
                    "applies_to": applies_to,
                    "date_updated": date_updated,
                    "reranker_score": cit.reranker_score,
                    "match_type": "verified"
                })

            # If On Your Data didn't return citations but we have an answer,
            # try to find supporting evidence via direct search
            if not evidence_items and found:
                logger.info("No citations from On Your Data, supplementing with search")
                extracted_refs = self._extract_policy_refs_from_response(answer_text)

                if extracted_refs:
                    for ref in extracted_refs[:3]:
                        try:
                            if ref['reference_number']:
                                # Wrap sync search in thread to avoid blocking
                                ref_results = await asyncio.to_thread(
                                    self.search_index.search,
                                    query=ref['reference_number'],
                                    top=3,
                                    filter_expr=filter_expr,
                                    use_semantic_ranking=True
                                )
                                for r in ref_results:
                                    if r.reference_number and (
                                        r.reference_number == ref['reference_number'] or
                                        ref['reference_number'] in r.reference_number
                                    ):
                                        title = _normalize_policy_title(r.title)
                                        evidence_items.append(
                                            EvidenceItem(
                                                snippet=_truncate_verbatim(r.content or ""),
                                                citation=r.citation,
                                                title=title,
                                                reference_number=r.reference_number,
                                                section=r.section,
                                                applies_to=r.applies_to,
                                                source_file=r.source_file,
                                                page_number=r.page_number,
                                                score=r.score,
                                                reranker_score=r.reranker_score,
                                                match_type="verified",
                                            )
                                        )
                                        sources.append({
                                            "citation": r.citation,
                                            "source_file": r.source_file,
                                            "title": title,
                                            "reference_number": r.reference_number,
                                            "section": r.section,
                                            "applies_to": r.applies_to,
                                            "score": r.score,
                                            "match_type": "verified"
                                        })
                                        break
                        except Exception as e:
                            logger.warning(f"Supplemental search failed for ref {ref}: {e}")

            # Extract clean quick answer for display
            clean_summary = _extract_quick_answer(answer_text)

            # Format the summary with bold citations and reference markers
            formatted_summary = _format_answer_with_citations(clean_summary, evidence_items)

            formatter = get_citation_formatter()
            found_flag = bool(evidence_items)
            formatted_result = formatter.format(
                answer_text=formatted_summary or clean_summary,
                evidence=evidence_items,
                max_refs=len(evidence_items) if evidence_items else 0,
                found=found_flag,
            )

            summary_payload = formatted_result.summary or formatted_summary
            response_payload = formatted_result.response or answer_text

            return ChatResponse(
                response=response_payload,
                summary=summary_payload,
                evidence=evidence_items,
                raw_response=str(result.raw_response),
                sources=sources,
                chunks_used=len(evidence_items),
                found=found_flag
            )

        except asyncio.TimeoutError:
            logger.warning("On Your Data request timed out after 45s")
            return await self._chat_with_standard_retrieval(request, filter_expr)
        except Exception as e:
            logger.warning(f"On Your Data failed, falling back to standard retrieval: {e}")
            return await self._chat_with_standard_retrieval(request, filter_expr)

    async def _chat_with_standard_retrieval(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """
        Handle chat using standard hybrid search retrieval.

        This is the fallback when On Your Data is not available.
        Returns search results with a basic "not found" message if no LLM configured.
        """
        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        try:
            # Wrap sync search in thread with 30s timeout to prevent hanging connections
            search_results = await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_index.search,
                    query=expanded_query,
                    top=5,
                    filter_expr=filter_expr,
                    use_semantic_ranking=True
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("Fallback search timed out after 30s")
            return ChatResponse(
                response="I'm sorry, the search is taking longer than expected. Please try again in a moment.",
                summary="Search timeout",
                evidence=[],
                sources=[],
                chunks_used=0,
                found=False
            )
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return ChatResponse(
                response="I'm sorry, I encountered an issue while searching the policy database. Please try again in a moment.",
                summary="Search temporarily unavailable",
                evidence=[],
                sources=[],
                chunks_used=0,
                found=False
            )

        if search_results is None:
            search_results = []

        context = format_rag_context(search_results) if search_results else ""
        evidence_items = build_supporting_evidence(search_results) if search_results else []

        sources = [{
            "citation": r.citation,
            "source_file": r.source_file,
            "title": r.title,
            "reference_number": r.reference_number,
            "section": r.section,
            "applies_to": r.applies_to,
            "date_updated": r.date_updated,
            "score": r.score,
            "document_owner": r.document_owner,
            "date_approved": r.date_approved
        } for r in search_results]

        # Without On Your Data, we can only return search results
        # The frontend should display these with a notice that LLM is unavailable
        if not search_results:
            summary_text = NOT_FOUND_MESSAGE
        else:
            summary_text = LLM_UNAVAILABLE_MESSAGE

        found = bool(search_results) and summary_text != NOT_FOUND_MESSAGE

        if not found:
            evidence_items = []
            sources = []

        return ChatResponse(
            response=summary_text,
            summary=summary_text,
            evidence=evidence_items,
            raw_response=summary_text,
            sources=sources,
            chunks_used=len(search_results),
            found=found
        )
