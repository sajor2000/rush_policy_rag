import asyncio
import logging
from typing import Optional, AsyncGenerator, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, status

from azure_policy_index import PolicySearchIndex
from app.services.on_your_data_service import OnYourDataService
from app.services.cohere_rerank_service import CohereRerankService
from app.services.chat_audit_service import init_chat_audit_service, shutdown_chat_audit_service
from app.core.config import settings
from app.core.auth import AzureADTokenValidator, TokenValidationError

logger = logging.getLogger(__name__)

# Global clients
_search_index: Optional[PolicySearchIndex] = None
_on_your_data_service: Optional[OnYourDataService] = None
_cohere_rerank_service: Optional[CohereRerankService] = None
_auth_validator: Optional[AzureADTokenValidator] = None

# Request tracking for graceful shutdown
_active_requests: int = 0
_request_lock = asyncio.Lock()


async def increment_requests() -> None:
    """Increment active request counter (thread-safe)."""
    global _active_requests
    async with _request_lock:
        _active_requests += 1


async def decrement_requests() -> None:
    """Decrement active request counter (thread-safe)."""
    global _active_requests
    async with _request_lock:
        _active_requests -= 1


async def get_active_request_count() -> int:
    """Get current number of active requests (thread-safe)."""
    async with _request_lock:
        return _active_requests


def get_search_index() -> PolicySearchIndex:
    global _search_index
    if _search_index is None:
        raise RuntimeError("Search index not initialized")
    return _search_index


def get_on_your_data_service_dep() -> Optional[OnYourDataService]:
    global _on_your_data_service
    return _on_your_data_service


def get_cohere_rerank_service() -> Optional[CohereRerankService]:
    """Get Cohere Rerank service (cross-encoder for negation-aware search)."""
    global _cohere_rerank_service
    return _cohere_rerank_service


def get_current_user_claims(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    """Validate Authorization header when Azure AD auth is required."""

    if not settings.REQUIRE_AAD_AUTH:
        return None

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1]

    validator = _get_auth_validator()
    if not validator:
        raise HTTPException(status_code=500, detail="Authentication not configured")

    try:
        return validator.validate(token)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )


def _get_auth_validator() -> Optional[AzureADTokenValidator]:
    global _auth_validator
    return _auth_validator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import time

    # Track total startup time for observability
    app_startup_start = time.perf_counter()
    logger.info("=" * 80)
    logger.info("🚀 APPLICATION STARTUP - Cold Start Optimization Enabled")
    logger.info("=" * 80)

    global _search_index, _on_your_data_service, _cohere_rerank_service, _auth_validator

    # Initialize to None
    _search_index = None
    _on_your_data_service = None
    _cohere_rerank_service = None
    _auth_validator = None

    # Initialize Search Index
    try:
        _search_index = PolicySearchIndex()
        logger.info(f"Search index client initialized: {_search_index.index_name}")
    except Exception as e:
        logger.error(f"Failed to initialize search index: {e}")
        raise

    # Initialize On Your Data service for vectorSemanticHybrid search
    try:
        if settings.USE_ON_YOUR_DATA:
            _on_your_data_service = OnYourDataService()
            if _on_your_data_service.is_configured:
                logger.info(
                    f"On Your Data service enabled - "
                    f"vectorSemanticHybrid with semantic config: {_on_your_data_service.semantic_config}"
                )
            else:
                logger.warning(
                    "USE_ON_YOUR_DATA enabled but service not configured - "
                    "check AOAI_ENDPOINT, AOAI_API_KEY, SEARCH_ENDPOINT, SEARCH_API_KEY"
                )
                _on_your_data_service = None
        else:
            logger.info("On Your Data (vectorSemanticHybrid) disabled")
    except Exception as e:
        logger.error(f"Failed to initialize On Your Data Service: {e}")
        _on_your_data_service = None

    # ===================================================================
    # EAGER INITIALIZE ALL LAZY SINGLETONS (Cold Start Optimization)
    # These services were previously initialized on first request.
    # Pre-initializing them during startup eliminates 300-700ms of first-query latency.
    # ===================================================================
    try:
        logger.info("Pre-initializing lazy-loaded services for cold start optimization...")
        startup_start = time.perf_counter()

        # Import and initialize all singleton services
        from app.services.synonym_service import get_synonym_service
        from app.services.query_decomposer import get_query_decomposer
        from app.services.citation_verifier import get_citation_verifier
        from app.services.safety_validator import get_safety_validator
        from app.services.corrective_rag import get_corrective_rag_service
        from app.services.self_reflective_rag import get_self_reflective_service
        from app.services.citation_formatter import get_citation_formatter

        # Initialize each service (calls __init__, loads files, compiles regexes, etc.)
        get_synonym_service()          # Loads 1MB JSON + builds 4 indexes (~50-150ms)
        get_query_decomposer()         # Regex compilation (~10-50ms)
        get_citation_verifier()        # Pattern compilation (~10-50ms)
        get_safety_validator()         # Rule loading (~10-50ms)
        get_corrective_rag_service()   # Model init (~20-100ms)
        get_self_reflective_service()  # (~20-100ms)
        get_citation_formatter()       # (~10-30ms)

        startup_elapsed = (time.perf_counter() - startup_start) * 1000
        logger.info(f"✅ All lazy services pre-initialized in {startup_elapsed:.1f}ms")

    except Exception as e:
        logger.warning(f"⚠️ Failed to pre-initialize some helper services (non-critical): {e}")

    # Initialize Cohere Rerank service (cross-encoder for negation-aware search)
    try:
        if settings.USE_COHERE_RERANK:
            _cohere_rerank_service = CohereRerankService(
                endpoint=settings.COHERE_RERANK_ENDPOINT,
                api_key=settings.COHERE_RERANK_API_KEY,
                top_n=settings.COHERE_RERANK_TOP_N,
                min_score=settings.COHERE_RERANK_MIN_SCORE,
                model_name=settings.COHERE_RERANK_MODEL
            )
            if _cohere_rerank_service.is_configured:
                logger.info(
                    f"Cohere Rerank service enabled (cross-encoder) - "
                    f"top_n={settings.COHERE_RERANK_TOP_N}, "
                    f"min_score={settings.COHERE_RERANK_MIN_SCORE}"
                )
            else:
                logger.warning(
                    "USE_COHERE_RERANK enabled but service not configured - "
                    "check COHERE_RERANK_ENDPOINT and COHERE_RERANK_API_KEY"
                )
                _cohere_rerank_service = None
        else:
            logger.info("Cohere Rerank disabled - using Azure L2 semantic reranker")
    except Exception as e:
        logger.error(f"Failed to initialize Cohere Rerank Service: {e}")
        _cohere_rerank_service = None

    # Initialize Azure AD validator if required
    try:
        if settings.REQUIRE_AAD_AUTH:
            audience = settings.AZURE_AD_TOKEN_AUDIENCE or settings.AZURE_AD_CLIENT_ID
            if not (settings.AZURE_AD_TENANT_ID and audience):
                raise RuntimeError("REQUIRE_AAD_AUTH enabled but Azure AD settings incomplete")

            _auth_validator = AzureADTokenValidator(
                tenant_id=settings.AZURE_AD_TENANT_ID,
                audience=audience,
                allowed_client_ids=settings.ALLOWED_AAD_CLIENT_IDS,
            )
            logger.info("Azure AD authentication enabled")
        else:
            _auth_validator = None
    except Exception as e:
        logger.error(f"Failed to initialize Azure AD auth validator: {e}")
        raise

    # ===================================================================
    # WARM UP AZURE AI SEARCH INDEX (Cold Start Optimization)
    # First search query incurs DNS resolution + TLS handshake (~200-500ms).
    # This warmup eliminates that latency by priming the connection pool.
    # ===================================================================
    if _search_index:
        try:
            logger.info("Warming up Azure AI Search index...")
            warmup_start = time.perf_counter()

            # Make minimal search request to prime connection pool
            # Using synchronous call wrapped in thread (search_index.search is sync)
            import asyncio
            await asyncio.to_thread(
                _search_index.search,
                query="system warmup",
                top=1  # Minimal result set
            )

            warmup_elapsed = (time.perf_counter() - warmup_start) * 1000
            logger.info(f"✅ Search index warmed up in {warmup_elapsed:.1f}ms - connection pool primed")

        except Exception as e:
            logger.warning(f"⚠️ Search index warmup failed (non-critical): {e}")

    # ===================================================================
    # PARALLEL WARMUP OF ALL AZURE SERVICES (Cold Start Optimization)
    # Previously: OnYourData (500ms) -> Cohere (150ms) = 650ms sequential
    # Now: Both run concurrently = ~500ms total (saves 150-200ms)
    # ===================================================================
    import asyncio

    warmup_tasks = []

    # Collect warmup tasks (only for configured services)
    if _on_your_data_service and _on_your_data_service.is_configured:
        warmup_tasks.append(("OnYourDataService", _on_your_data_service.warmup()))

    if _cohere_rerank_service and _cohere_rerank_service.is_configured:
        warmup_tasks.append(("CohereRerankService", _cohere_rerank_service.warmup()))

    # Run all warmups in parallel
    if warmup_tasks:
        logger.info(f"Starting {len(warmup_tasks)} service warmups in parallel...")
        parallel_start = time.perf_counter()

        # Execute all warmups concurrently
        results = await asyncio.gather(
            *[task for _, task in warmup_tasks],
            return_exceptions=True  # Don't fail startup if one warmup fails
        )

        parallel_elapsed = (time.perf_counter() - parallel_start) * 1000

        # Log results
        for (service_name, _), result in zip(warmup_tasks, results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ {service_name} warmup failed (non-critical): {result}")
            elif result:
                logger.info(f"✅ {service_name} warmed up successfully")
            else:
                logger.warning(f"⚠️ {service_name} warmup returned False (non-critical)")

        logger.info(f"✅ All warmups completed in {parallel_elapsed:.1f}ms (parallel execution)")

    # Initialize Chat Audit Service for RAG quality monitoring
    try:
        if settings.CHAT_AUDIT_ENABLED:
            await init_chat_audit_service()
            logger.info("Chat Audit Service initialized - logging to blob storage")
        else:
            logger.info("Chat Audit Service disabled")
    except Exception as e:
        # Non-critical - don't fail startup if audit service fails
        logger.warning(f"Failed to initialize Chat Audit Service (non-critical): {e}")

    # Log total startup time
    app_startup_elapsed = (time.perf_counter() - app_startup_start) * 1000
    logger.info("=" * 80)
    logger.info(f"✅ APPLICATION STARTUP COMPLETE - Total time: {app_startup_elapsed:.1f}ms")
    logger.info("=" * 80)

    yield

    # Graceful shutdown - wait for in-flight requests to complete
    logger.info("Initiating graceful shutdown...")
    shutdown_start = asyncio.get_event_loop().time()
    max_wait = 25  # Leave 5s buffer before container kill (default 30s)

    while _active_requests > 0:
        elapsed = asyncio.get_event_loop().time() - shutdown_start
        if elapsed > max_wait:
            logger.warning(
                f"Shutdown timeout after {max_wait}s - "
                f"{_active_requests} requests still active, proceeding with cleanup"
            )
            break
        logger.info(f"Waiting for {_active_requests} active request(s) to complete...")
        await asyncio.sleep(0.5)

    if _active_requests == 0:
        logger.info("All requests completed, proceeding with cleanup")

    # Shutdown - clean up all service connections
    logger.info("Application shutting down - cleaning up resources")

    # Close On Your Data service
    if _on_your_data_service:
        try:
            _on_your_data_service.close()
            logger.info("On Your Data service closed")
        except Exception as e:
            logger.warning(f"Error closing On Your Data service: {e}")

    # Close Cohere Rerank service (async for proper cleanup)
    if _cohere_rerank_service:
        try:
            await _cohere_rerank_service.aclose()
            logger.info("Cohere Rerank service closed")
        except Exception as e:
            logger.warning(f"Error closing Cohere Rerank service: {e}")

    # Close search index
    if _search_index:
        try:
            _search_index.close()
            logger.info("Search index closed")
        except Exception as e:
            logger.warning(f"Error closing search index: {e}")

    # Stop Chat Audit Service and flush remaining records
    try:
        await shutdown_chat_audit_service()
        logger.info("Chat Audit Service stopped - all records flushed")
    except Exception as e:
        logger.warning(f"Error stopping Chat Audit Service: {e}")

    logger.info("All resources cleaned up")
