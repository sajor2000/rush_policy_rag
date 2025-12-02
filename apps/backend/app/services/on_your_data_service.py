"""
Azure OpenAI "On Your Data" Service

Provides full vectorSemanticHybrid search support that the azure-ai-agents SDK lacks.
Uses the Azure OpenAI Chat Completions API with data_sources parameter.

Key advantage: Proper semantic_configuration parameter support for L2 reranking.
"""

import os
import logging
from typing import Optional, List
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from openai import AzureOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

logger = logging.getLogger(__name__)

# SSL verification toggle for corporate proxy environments (e.g., Netskope)
# Set DISABLE_SSL_VERIFY=true in .env for development on corporate machines
DISABLE_SSL_VERIFY = os.environ.get("DISABLE_SSL_VERIFY", "false").lower() == "true"

# Retry configuration for Azure OpenAI calls
RETRY_EXCEPTIONS = (RateLimitError, APITimeoutError, APIConnectionError)


@dataclass
class OnYourDataReference:
    """A reference/citation from Azure OpenAI On Your Data response."""
    content: str
    title: str
    filepath: str = ""
    url: str = ""
    chunk_id: str = ""
    reference_number: str = ""
    section: str = ""
    applies_to: str = ""
    reranker_score: Optional[float] = None


@dataclass
class OnYourDataResult:
    """Result from Azure OpenAI On Your Data chat."""
    answer: str
    citations: List[OnYourDataReference]
    intent: str = ""
    raw_response: Optional[dict] = None


class OnYourDataService:
    """
    Azure OpenAI 'On Your Data' service with full vectorSemanticHybrid support.

    This replaces FoundryAgentService to enable proper semantic hybrid search:
    - Vector search (embedding similarity)
    - BM25 keyword search
    - L2 semantic reranking (requires semantic_configuration)

    The azure-ai-agents SDK (v1.2.0b6) doesn't expose semantic_configuration_name,
    causing VECTOR_SEMANTIC_HYBRID to fail. This service uses the Chat Completions
    API with data_sources which properly supports all parameters.
    """

    def __init__(self):
        # Azure OpenAI configuration
        self.endpoint = os.environ.get("AOAI_ENDPOINT")
        self.api_key = os.environ.get("AOAI_API")
        self.api_version = os.environ.get("AOAI_API_VERSION", "2024-08-01-preview")
        self.model = os.environ.get("AOAI_CHAT_DEPLOYMENT", "gpt-4.1")

        # Azure AI Search configuration
        self.search_endpoint = os.environ.get("SEARCH_ENDPOINT")
        self.search_key = os.environ.get("SEARCH_API_KEY")
        self.index_name = os.environ.get("SEARCH_INDEX_NAME", "rush-policies")
        self.semantic_config = os.environ.get("SEARCH_SEMANTIC_CONFIG", "default-semantic")
        self.embedding_deployment = os.environ.get("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

        # Initialize Azure OpenAI client with connection pooling and optimized timeouts
        # Connection pooling reduces cold-start latency on subsequent requests
        # Timeout breakdown: connect=10s, read=60s (allows for embedding + search + generation)
        if self.endpoint and self.api_key:
            # Configure HTTP client with connection pooling for better performance
            http_client = httpx.Client(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=30.0  # Keep connections alive for 30s
                )
            )
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
                http_client=http_client,
                timeout=60.0  # Increased from 45s to handle cold starts
            )
            self._http_client = http_client  # Keep reference for cleanup
            logger.info(f"Initialized AzureOpenAI client with connection pooling: {self.endpoint}")
            logger.info(f"Search index: {self.index_name}, semantic config: {self.semantic_config}")
        else:
            self.client = None
            self._http_client = None
            logger.warning("Azure OpenAI credentials not configured (AOAI_ENDPOINT, AOAI_API)")

        # Load system prompt
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """Load the RISEN system prompt from file."""
        prompt_path = Path(__file__).resolve().parent.parent.parent / "policytech_prompt.txt"
        if prompt_path.exists():
            with open(prompt_path, 'r') as f:
                return f.read()

        # Fallback minimal prompt
        return """You are PolicyTech, RUSH's policy expert assistant.
Answer questions using ONLY the retrieved policy documents.
Always cite the policy title and reference number.
If the information is not in the provided documents, say so."""

    @property
    def is_configured(self) -> bool:
        """Check if service is properly configured."""
        return (
            self.client is not None and
            self.search_endpoint is not None and
            self.search_key is not None
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(RETRY_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def chat(
        self,
        query: str,
        system_prompt: Optional[str] = None,
        filter_expr: Optional[str] = None,
        top_n_documents: int = 50,
        strictness: int = 3,
        temperature: float = 0.1,
        max_tokens: int = 1000
    ) -> OnYourDataResult:
        """
        Query with full vectorSemanticHybrid search.

        This enables:
        - Vector similarity search (text-embedding-3-large)
        - BM25 keyword matching
        - L2 semantic reranking (the key feature missing in Foundry Agents)

        Retry behavior:
        - Retries up to 3 times on rate limit (429), timeout, or connection errors
        - Uses exponential backoff (2s, 4s, 8s) between retries

        Args:
            query: User's question
            system_prompt: Override system prompt (uses RISEN prompt by default)
            filter_expr: OData filter expression for Azure AI Search
            top_n_documents: Number of documents to retrieve (default 50 for reranker)
            strictness: How strictly to ground responses (1-5, default 3)
            temperature: Response temperature (default 0.1 for accuracy)
            max_tokens: Maximum response tokens

        Returns:
            OnYourDataResult with answer, citations, and raw response
        """
        if not self.is_configured:
            raise RuntimeError(
                "OnYourDataService not configured. "
                "Check AOAI_ENDPOINT, AOAI_API, SEARCH_ENDPOINT, SEARCH_API_KEY"
            )

        prompt = system_prompt or self.system_prompt

        # Build the data_sources configuration for Azure AI Search
        parameters = {
            "endpoint": self.search_endpoint,
            "index_name": self.index_name,
            "authentication": {
                "type": "api_key",
                "key": self.search_key
            },
            # ✅ FULL SEMANTIC HYBRID SEARCH - the key fix!
            # Set DISABLE_SEMANTIC_SEARCH=true to fall back to vector+BM25 hybrid (no semantic reranking)
            "query_type": "vector_simple_hybrid" if os.getenv("DISABLE_SEMANTIC_SEARCH", "").lower() == "true" else "vector_semantic_hybrid",
            # Embedding configuration for vector search
            "embedding_dependency": {
                "type": "deployment_name",
                "deployment_name": self.embedding_deployment
            },
            # Field mappings for our rush-policies index
            # Note: url_field omitted (Azure API expects string, not None)
            "fields_mapping": {
                "content_fields": ["content"],
                "title_field": "title",
                "filepath_field": "source_file",
                "vector_fields": ["content_vector"]
            },
            # Retrieval parameters
            "top_n_documents": top_n_documents,
            "strictness": strictness,
            "in_scope": True  # Only use retrieved documents
        }

        # Conditionally add semantic_configuration (only when semantic search is enabled)
        disable_semantic = os.getenv("DISABLE_SEMANTIC_SEARCH", "").lower() == "true"
        if not disable_semantic:
            parameters["semantic_configuration"] = self.semantic_config

        # Conditionally add filter (Azure API expects string type, not None)
        if filter_expr:
            parameters["filter"] = filter_expr

        data_sources = [{
            "type": "azure_search",
            "parameters": parameters
        }]

        query_type = parameters["query_type"]
        logger.info(
            f"OnYourData query: '{query[:50]}...' "
            f"(query_type={query_type}, semantic_config={'disabled' if disable_semantic else self.semantic_config}, "
            f"top_n={top_n_documents})"
        )

        try:
            # Call Azure OpenAI Chat Completions with data_sources
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query}
                ],
                extra_body={"data_sources": data_sources},
                temperature=temperature,
                max_tokens=max_tokens
            )

            # Extract the response
            message = response.choices[0].message
            answer = message.content or ""

            # Extract citations from the context
            citations = []
            intent = ""

            # Azure OpenAI "On Your Data" returns context in message.context
            if hasattr(message, 'context') and message.context:
                context_data = message.context

                # Extract intent if available
                intent = context_data.get('intent', '') if isinstance(context_data, dict) else ''

                # Extract citations
                raw_citations = []
                if isinstance(context_data, dict):
                    raw_citations = context_data.get('citations', [])
                elif hasattr(context_data, 'citations'):
                    raw_citations = context_data.citations or []

                for cit in raw_citations:
                    if isinstance(cit, dict):
                        citations.append(OnYourDataReference(
                            content=cit.get('content', ''),
                            title=cit.get('title', ''),
                            filepath=cit.get('filepath', ''),
                            url=cit.get('url', ''),
                            chunk_id=cit.get('chunk_id', ''),
                            reranker_score=cit.get('reranker_score')
                        ))
                    elif hasattr(cit, 'content'):
                        citations.append(OnYourDataReference(
                            content=getattr(cit, 'content', ''),
                            title=getattr(cit, 'title', ''),
                            filepath=getattr(cit, 'filepath', ''),
                            url=getattr(cit, 'url', ''),
                            chunk_id=getattr(cit, 'chunk_id', ''),
                            reranker_score=getattr(cit, 'reranker_score', None)
                        ))

            logger.info(
                f"OnYourData response: {len(answer)} chars, "
                f"{len(citations)} citations, intent='{intent[:30]}...'"
            )

            return OnYourDataResult(
                answer=answer,
                citations=citations,
                intent=intent,
                raw_response=response.model_dump() if hasattr(response, 'model_dump') else str(response)
            )

        except RateLimitError as e:
            logger.warning(
                f"Azure OpenAI rate limited: {e}. "
                f"Retry-After: {getattr(e, 'response', {}).headers.get('Retry-After', 'N/A') if hasattr(e, 'response') else 'N/A'}"
            )
            raise  # Let tenacity retry handle it
        except APITimeoutError as e:
            logger.warning(f"Azure OpenAI timeout: {e}")
            raise  # Let tenacity retry handle it
        except APIConnectionError as e:
            logger.warning(f"Azure OpenAI connection error: {e}")
            raise  # Let tenacity retry handle it
        except Exception as e:
            logger.error(f"OnYourData chat failed (non-retryable): {e}")
            raise

    async def retrieve(
        self,
        query: str,
        max_results: int = 5
    ) -> OnYourDataResult:
        """
        Compatibility method matching FoundryAgentService.retrieve() signature.

        This allows OnYourDataService to be used as a drop-in replacement.
        """
        return await self.chat(
            query=query,
            top_n_documents=50,  # Get many for reranker
            strictness=3
        )

    async def warmup(self) -> bool:
        """
        Warm up the service by making a minimal request.
        
        This primes the connection pool and reduces cold-start latency
        for subsequent requests. Should be called during app startup.
        
        Returns:
            True if warmup succeeded, False otherwise
        """
        if not self.is_configured:
            logger.warning("Cannot warm up OnYourDataService - not configured")
            return False
        
        try:
            logger.info("Warming up OnYourDataService...")
            # Make a minimal request to prime connections
            await self.chat(
                query="warmup ping",
                top_n_documents=1,
                strictness=5,  # Maximum strictness to minimize processing
                max_tokens=10
            )
            logger.info("OnYourDataService warmup completed successfully")
            return True
        except Exception as e:
            logger.warning(f"OnYourDataService warmup failed (non-critical): {e}")
            return False

    def close(self) -> None:
        """
        Clean up resources.

        Should be called during application shutdown to release connections.
        """
        # Close HTTP client first (connection pool)
        if self._http_client is not None:
            try:
                self._http_client.close()
                logger.info("OnYourDataService HTTP client closed")
            except Exception as e:
                logger.warning(f"Error closing HTTP client: {e}")
            self._http_client = None
        
        if self.client is not None:
            try:
                self.client.close()
                logger.info("OnYourDataService client closed")
            except Exception as e:
                logger.warning(f"Error closing OnYourDataService client: {e}")
            self.client = None
