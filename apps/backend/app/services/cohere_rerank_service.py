"""
Cohere Rerank Service

Provides cross-encoder reranking for negation-aware search.
Uses Cohere Rerank 3.5 deployed on Azure AI Foundry.

Why cross-encoder beats Azure's L2 semantic reranker for negation:
- Azure L2 is a bi-encoder: embeds query and document separately, then compares
- Cohere Rerank is a cross-encoder: processes query + document together
- Cross-encoders can understand "NOT authorized" contradicts "Can accept verbal orders?"
- Bi-encoders only see vocabulary overlap, missing logical negation

Best Practices (per Cohere docs):
- Use YAML format for structured documents with sort_keys=False
- Field order matters: put most important fields first (title, ref#), content last
- Content field is most likely to be truncated at 4096 token context limit
- Relevance scores are normalized [0,1] - use threshold to filter low-relevance docs
"""

import logging
import httpx
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import yaml
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    AsyncRetrying
)

logger = logging.getLogger(__name__)

# Default minimum relevance score threshold (per Cohere best practices)
# Documents below this score are filtered out as low-relevance
DEFAULT_MIN_SCORE = 0.1


@dataclass
class RerankResult:
    """A document with its Cohere rerank score."""
    content: str
    title: str
    reference_number: str
    source_file: str
    section: str = ""
    applies_to: str = ""
    cohere_score: float = 0.0
    original_index: int = 0


class CohereRerankService:
    """
    Cohere Rerank service using cross-encoder for negation-aware search.

    Deployed on Azure AI Foundry as a serverless API.
    Pricing: ~$1 per 1000 searches (100 docs = 1 search unit)
    
    Best Practices:
    - Context length: 4096 tokens (query can use up to 2048)
    - YAML format for structured data preserves field relationships
    - Field order: title, reference_number, section, applies_to, content (last)
    - Score threshold filters low-relevance results
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        top_n: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
        model_name: str = "cohere-rerank-v3-5"
    ):
        """
        Initialize Cohere client for Azure AI Foundry deployment.

        Args:
            endpoint: Azure AI Foundry endpoint URL (with or without /v1/rerank)
                      e.g., https://Cohere-rerank-v3-5-beomo.eastus2.models.ai.azure.com/v1/rerank
            api_key: API key from Azure AI Foundry deployment
            top_n: Default number of documents to return after reranking
            min_score: Minimum relevance score threshold (0.0-1.0). Documents below
                       this score are filtered out. Per Cohere best practices.
            model_name: Cohere model name (default: cohere-rerank-v3-5)
        """
        self.top_n = top_n
        self.min_score = min_score
        self.model_name = model_name
        self._client = None
        self._async_client = None
        self._configured = False

        if endpoint and api_key:
            # Normalize endpoint - ensure it ends with /v1/rerank
            self.endpoint = endpoint.rstrip('/')
            if not self.endpoint.endswith('/v1/rerank'):
                self.endpoint = f"{self.endpoint}/v1/rerank"
            self.api_key = api_key

            # Headers for Azure AI Foundry
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Bearer {api_key}'
            }

            # Sync client (for backwards compatibility)
            self._client = httpx.Client(
                timeout=30.0,
                headers=headers
            )
            
            # Async client with connection pooling for better performance
            self._async_client = httpx.AsyncClient(
                timeout=30.0,
                headers=headers,
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=30.0
                )
            )
            
            self._configured = True
            logger.info(
                f"Initialized Cohere Rerank client (Azure AI Foundry): {self.endpoint}, "
                f"top_n={top_n}, min_score={min_score}"
            )
        else:
            self.endpoint = ""
            self.api_key = ""
            logger.warning("Cohere Rerank credentials not configured")

    @property
    def is_configured(self) -> bool:
        """Check if service is properly configured."""
        return self._configured and self._client is not None

    def _format_documents_as_yaml(self, documents: List[Dict[str, Any]]) -> List[str]:
        """
        Format documents as YAML for Cohere Rerank.
        
        Per Cohere best practices:
        - YAML format preserves field structure for better reranking
        - Field order matters: most important fields first, content last
        - Content is most likely to be truncated at 4096 token limit
        - Use sort_keys=False to maintain field order
        
        Healthcare-optimized field order for RUSH policy documents:
        1. policy_title - Primary identifier
        2. reference_number - Critical for citation accuracy
        3. applies_to_entities - RUMC, RUMG, ROPH, etc.
        4. section - Document structure
        5. document_owner - Accountability
        6. effective_date - Currency of information
        7. content - LAST (truncation safe)
        """
        doc_texts = []
        for doc in documents:
            # Healthcare-optimized field order for policy reranking
            doc_repr = {
                "policy_title": doc.get("title", ""),
                "reference_number": doc.get("reference_number", ""),
            }
            # Add healthcare-specific fields if present
            if doc.get("applies_to"):
                doc_repr["applies_to_entities"] = doc.get("applies_to")
            if doc.get("section"):
                doc_repr["section"] = doc.get("section")
            if doc.get("document_owner"):
                doc_repr["document_owner"] = doc.get("document_owner")
            if doc.get("date_updated"):
                doc_repr["effective_date"] = doc.get("date_updated")
            # Content LAST - most likely to be truncated at 4096 token limit
            doc_repr["content"] = doc.get("content", "")
            
            doc_texts.append(yaml.dump(doc_repr, sort_keys=False, default_flow_style=False))
        return doc_texts

    def _log_score_distribution(self, results: List[RerankResult], query: str) -> None:
        """
        Log score distribution for threshold calibration analysis.
        
        Per Cohere best practices, score thresholds should be calibrated
        on domain-specific queries. This logging helps identify optimal
        thresholds for healthcare policy retrieval.
        
        Uses DEBUG level to avoid production log spam.
        """
        if not results:
            return
        
        scores = [r.cohere_score for r in results]
        sorted_scores = sorted(scores, reverse=True)
        
        logger.debug(
            f"Cohere score distribution for '{query[:40]}...': "
            f"min={min(scores):.3f}, max={max(scores):.3f}, "
            f"mean={sum(scores)/len(scores):.3f}, "
            f"median={sorted_scores[len(scores)//2]:.3f}, "
            f"count={len(scores)}"
        )

    def _build_results(
        self,
        result_data: Dict[str, Any],
        documents: List[Dict[str, Any]],
        min_score: Optional[float] = None
    ) -> List[RerankResult]:
        """Build RerankResult list from API response with score filtering."""
        threshold = min_score if min_score is not None else self.min_score
        reranked = []
        filtered_count = 0
        
        for result in result_data.get("results", []):
            idx = result.get("index", 0)
            score = result.get("relevance_score", 0.0)
            
            # Filter low-relevance documents (per Cohere best practices)
            if score < threshold:
                filtered_count += 1
                continue
                
            original_doc = documents[idx]
            reranked.append(RerankResult(
                content=original_doc.get("content", ""),
                title=original_doc.get("title", ""),
                reference_number=original_doc.get("reference_number", ""),
                source_file=original_doc.get("source_file", ""),
                section=original_doc.get("section", ""),
                applies_to=original_doc.get("applies_to", ""),
                cohere_score=score,
                original_index=idx
            ))
        
        if filtered_count > 0:
            logger.info(f"Filtered {filtered_count} docs below score threshold {threshold}")
        
        return reranked

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError,)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_n: Optional[int] = None,
        min_score: Optional[float] = None
    ) -> List[RerankResult]:
        """
        Rerank documents using Cohere cross-encoder via Azure AI Foundry (sync).

        The cross-encoder processes query + document together, enabling it
        to understand logical negation and contextual relationships.

        Args:
            query: User's search query
            documents: List of documents with 'content', 'title', 'reference_number', etc.
            top_n: Number of documents to return (default: self.top_n)
            min_score: Minimum relevance score threshold (default: self.min_score)

        Returns:
            List of RerankResult sorted by relevance (highest first), filtered by min_score
        """
        if not self.is_configured:
            raise RuntimeError(
                "CohereRerankService not configured. "
                "Check COHERE_RERANK_ENDPOINT and COHERE_RERANK_API_KEY"
            )

        if not documents:
            logger.warning("No documents provided for reranking")
            return []

        n = top_n or self.top_n
        doc_texts = self._format_documents_as_yaml(documents)

        logger.info(
            f"Cohere rerank: query='{query[:50]}...' "
            f"docs={len(documents)} → top_n={n}"
        )

        try:
            payload = {
                "model": self.model_name,
                "query": query,
                "documents": doc_texts,
                "top_n": n,
                "return_documents": False,
                "max_tokens_per_doc": 2048
            }

            response = self._client.post(self.endpoint, json=payload)
            response.raise_for_status()
            result_data = response.json()

            reranked = self._build_results(result_data, documents, min_score)

            # Log score distribution for threshold calibration analysis
            self._log_score_distribution(reranked, query)

            # Log top results for debugging
            if reranked:
                top_refs = [f"{r.reference_number}({r.cohere_score:.4f})" for r in reranked[:3]]
                logger.info(f"Cohere rerank top-3: {', '.join(top_refs)}")

            return reranked

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"Cohere rate limited: {e}")
                raise  # Let tenacity retry
            logger.error(f"Cohere rerank HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Cohere rerank failed: {e}")
            raise

    async def rerank_async(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_n: Optional[int] = None,
        min_score: Optional[float] = None
    ) -> List[RerankResult]:
        """
        Rerank documents using Cohere cross-encoder via Azure AI Foundry (async).

        Uses native async httpx client for better performance in async contexts.
        Includes retry logic with exponential backoff for transient failures.

        Args:
            query: User's search query
            documents: List of documents with 'content', 'title', 'reference_number', etc.
            top_n: Number of documents to return (default: self.top_n)
            min_score: Minimum relevance score threshold (default: self.min_score)

        Returns:
            List of RerankResult sorted by relevance (highest first), filtered by min_score
        """
        if not self.is_configured or not self._async_client:
            raise RuntimeError(
                "CohereRerankService not configured. "
                "Check COHERE_RERANK_ENDPOINT and COHERE_RERANK_API_KEY"
            )

        if not documents:
            logger.warning("No documents provided for reranking")
            return []

        n = top_n or self.top_n
        doc_texts = self._format_documents_as_yaml(documents)

        logger.info(
            f"Cohere rerank (async): query='{query[:50]}...' "
            f"docs={len(documents)} → top_n={n}"
        )

        payload = {
            "model": self.model_name,
            "query": query,
            "documents": doc_texts,
            "top_n": n,
            "return_documents": False,
            "max_tokens_per_doc": 2048
        }

        # Async retry with exponential backoff (matches sync rerank behavior)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((httpx.HTTPStatusError,)),
            reraise=True
        ):
            with attempt:
                try:
                    response = await self._async_client.post(self.endpoint, json=payload)
                    response.raise_for_status()
                    result_data = response.json()

                    reranked = self._build_results(result_data, documents, min_score)

                    # Log score distribution at DEBUG level (avoid log spam)
                    self._log_score_distribution(reranked, query)

                    if reranked:
                        top_refs = [f"{r.reference_number}({r.cohere_score:.4f})" for r in reranked[:3]]
                        logger.info(f"Cohere rerank (async) top-3: {', '.join(top_refs)}")

                    return reranked

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        logger.warning(f"Cohere rate limited (attempt {attempt.retry_state.attempt_number}): {e}")
                        raise  # Let tenacity retry
                    logger.error(f"Cohere rerank HTTP error: {e.response.status_code} - {e.response.text}")
                    raise
                except Exception as e:
                    logger.error(f"Cohere rerank (async) failed: {e}")
                    raise
        
        # Should never reach here due to reraise=True
        return []

    async def warmup(self) -> bool:
        """
        Warm up the service by making a minimal request.
        
        Primes the connection pool to reduce cold-start latency
        for subsequent requests. Should be called during app startup.
        
        Returns:
            True if warmup succeeded, False otherwise
        """
        if not self.is_configured:
            logger.warning("Cannot warm up CohereRerankService - not configured")
            return False
        
        try:
            logger.info("Warming up CohereRerankService...")
            # Minimal request to prime connections
            await self.rerank_async(
                query="warmup",
                documents=[{"content": "test", "title": "warmup", "reference_number": "0"}],
                top_n=1,
                min_score=0.0  # Don't filter warmup request
            )
            logger.info("CohereRerankService warmup completed successfully")
            return True
        except Exception as e:
            logger.warning(f"CohereRerankService warmup failed (non-critical): {e}")
            return False

    def close(self) -> None:
        """Clean up resources (sync client only)."""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._configured = False
        logger.info("CohereRerankService sync client closed")

    async def aclose(self) -> None:
        """Clean up resources (async client)."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self._configured = False
        logger.info("CohereRerankService closed (sync and async)")
