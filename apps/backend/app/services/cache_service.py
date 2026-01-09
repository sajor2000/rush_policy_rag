"""
Cache Service for RUSH Policy RAG System

Provides multi-layer in-memory caching to reduce latency while maintaining accuracy:
- Layer 1: Query expansion cache (LRU, ~5000 entries, ~500KB)
- Layer 2: Response cache (TTL 24h, ~1000 entries, ~20MB)
- Layer 3: Search results cache (TTL 6h, ~500 entries, ~25MB)

Thread-safe for async FastAPI using threading.Lock.
Total memory budget: ~50MB
"""

import hashlib
import threading
import logging
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime

from cachetools import LRUCache, TTLCache

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """Cache statistics for monitoring."""
    hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses


class QueryNormalizer:
    """Normalize queries for cache key generation."""

    # Common words to ignore for similarity matching
    STOP_WORDS = frozenset([
        'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been',
        'what', 'how', 'when', 'where', 'who', 'which', 'why',
        'do', 'does', 'did', 'can', 'could', 'should', 'would',
        'policy', 'policies', 'procedure', 'procedures'
    ])

    @staticmethod
    def normalize(query: str) -> str:
        """
        Normalize query for cache key.

        Normalization rules (preserve semantic meaning):
        1. Lowercase
        2. Strip leading/trailing whitespace
        3. Collapse multiple spaces to single space
        4. Remove possessives ('s, ')
        5. Sort words alphabetically (for permutation-invariant matching)
        """
        if not query:
            return ""

        # Basic normalization
        normalized = query.lower().strip()
        normalized = ' '.join(normalized.split())  # Collapse whitespace

        # Remove possessives
        normalized = normalized.replace("'s", "").replace("'", "")

        # Remove punctuation except hyphens (preserve compound terms)
        normalized = ''.join(
            c if c.isalnum() or c in (' ', '-') else ' '
            for c in normalized
        )
        normalized = ' '.join(normalized.split())  # Collapse again

        # Sort words for permutation-invariant matching
        # "verbal orders policy" == "policy verbal orders"
        words = sorted(normalized.split())

        return ' '.join(words)

    @staticmethod
    def cache_key(query: str, filter_expr: Optional[str] = None) -> str:
        """Generate cache key from normalized query and filter."""
        normalized = QueryNormalizer.normalize(query)

        # Include filter in key if present
        if filter_expr:
            key_input = f"{normalized}|{filter_expr}"
        else:
            key_input = normalized

        # Use MD5 hash for compact keys (32 chars)
        return hashlib.md5(key_input.encode()).hexdigest()

    @staticmethod
    def search_cache_key(
        query: str,
        filter_expr: Optional[str],
        top_k: int
    ) -> str:
        """Generate cache key for search results."""
        # Don't normalize search query as much - preserve original intent
        key_input = f"{query.lower().strip()}|{filter_expr or ''}|{top_k}"
        return hashlib.md5(key_input.encode()).hexdigest()


class CacheService:
    """
    Centralized cache service for RAG pipeline.

    Thread-safe implementation using threading.Lock for async contexts.
    """

    # Default cache sizes (can be overridden via config)
    DEFAULT_EXPANSION_CACHE_SIZE = 5000   # ~500KB
    DEFAULT_RESPONSE_CACHE_SIZE = 1000    # ~20MB
    DEFAULT_SEARCH_CACHE_SIZE = 500       # ~25MB
    DEFAULT_RESPONSE_TTL = 86400          # 24 hours
    DEFAULT_SEARCH_TTL = 21600            # 6 hours

    def __init__(
        self,
        expansion_cache_size: int = DEFAULT_EXPANSION_CACHE_SIZE,
        response_cache_size: int = DEFAULT_RESPONSE_CACHE_SIZE,
        search_cache_size: int = DEFAULT_SEARCH_CACHE_SIZE,
        response_ttl: int = DEFAULT_RESPONSE_TTL,
        search_ttl: int = DEFAULT_SEARCH_TTL,
        enabled: bool = True
    ):
        self._enabled = enabled

        # Layer 1: Query expansion cache (no TTL, LRU eviction)
        self._expansion_cache: LRUCache = LRUCache(maxsize=expansion_cache_size)
        self._expansion_lock = threading.Lock()
        self._expansion_stats = CacheStats()

        # Layer 2: Full response cache (with TTL)
        self._response_cache: TTLCache = TTLCache(
            maxsize=response_cache_size,
            ttl=response_ttl
        )
        self._response_lock = threading.Lock()
        self._response_stats = CacheStats()

        # Layer 3: Search results cache (with TTL)
        self._search_cache: TTLCache = TTLCache(
            maxsize=search_cache_size,
            ttl=search_ttl
        )
        self._search_lock = threading.Lock()
        self._search_stats = CacheStats()

        # Cache version for invalidation tracking
        self._cache_version = datetime.utcnow().isoformat()
        self._invalidation_count = 0

        logger.info(
            f"CacheService initialized: enabled={enabled}, "
            f"expansion={expansion_cache_size}, "
            f"response={response_cache_size} (TTL={response_ttl}s), "
            f"search={search_cache_size} (TTL={search_ttl}s)"
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        logger.info(f"CacheService enabled={value}")

    # =========================================================================
    # Layer 1: Query Expansion Cache
    # =========================================================================

    def get_expansion(self, query: str) -> Optional[Tuple[str, Any]]:
        """
        Get cached query expansion result.

        Returns:
            Tuple of (expanded_query, QueryExpansion object) or None if not cached
        """
        if not self._enabled:
            return None

        key = QueryNormalizer.normalize(query)

        with self._expansion_lock:
            result = self._expansion_cache.get(key)
            if result is not None:
                self._expansion_stats.hits += 1
                logger.debug(f"Expansion cache HIT: {key[:30]}...")
                return result
            self._expansion_stats.misses += 1
            return None

    def set_expansion(self, query: str, expanded: str, expansion_obj: Any) -> None:
        """Cache query expansion result."""
        if not self._enabled:
            return

        key = QueryNormalizer.normalize(query)

        with self._expansion_lock:
            self._expansion_cache[key] = (expanded, expansion_obj)
            logger.debug(f"Expansion cache SET: {key[:30]}...")

    # =========================================================================
    # Layer 2: Response Cache
    # =========================================================================

    def get_response(
        self,
        query: str,
        filter_expr: Optional[str] = None
    ) -> Optional[Any]:
        """
        Get cached full response.

        Returns:
            ChatResponse object or None if not cached
        """
        if not self._enabled:
            return None

        key = QueryNormalizer.cache_key(query, filter_expr)

        with self._response_lock:
            result = self._response_cache.get(key)
            if result is not None:
                self._response_stats.hits += 1
                logger.info(f"Response cache HIT: {key[:16]}... (query: {query[:50]}...)")
                return result
            self._response_stats.misses += 1
            return None

    def set_response(
        self,
        query: str,
        response: Any,
        filter_expr: Optional[str] = None
    ) -> None:
        """Cache full response."""
        if not self._enabled:
            return

        key = QueryNormalizer.cache_key(query, filter_expr)

        with self._response_lock:
            self._response_cache[key] = response
            logger.debug(f"Response cache SET: {key[:16]}... (query: {query[:50]}...)")

    def should_cache_response(self, response: Any) -> bool:
        """
        Determine if a response should be cached.

        Only cache successful responses with evidence to avoid caching:
        - Error responses
        - "Not found" responses
        - Clarification requests
        """
        if not hasattr(response, 'found'):
            return False

        # Only cache if found=True and has evidence
        if not response.found:
            return False

        if hasattr(response, 'evidence') and not response.evidence:
            return False

        # Don't cache clarification responses
        if hasattr(response, 'confidence') and response.confidence == 'clarification_needed':
            return False

        return True

    # =========================================================================
    # Layer 3: Search Results Cache
    # =========================================================================

    def get_search_results(
        self,
        expanded_query: str,
        filter_expr: Optional[str] = None,
        top_k: int = 100
    ) -> Optional[List[Any]]:
        """
        Get cached search results.

        Returns:
            List of SearchResult objects or None if not cached
        """
        if not self._enabled:
            return None

        key = QueryNormalizer.search_cache_key(expanded_query, filter_expr, top_k)

        with self._search_lock:
            result = self._search_cache.get(key)
            if result is not None:
                self._search_stats.hits += 1
                logger.info(f"Search cache HIT: {key[:16]}... ({len(result)} results)")
                return result
            self._search_stats.misses += 1
            return None

    def set_search_results(
        self,
        expanded_query: str,
        results: List[Any],
        filter_expr: Optional[str] = None,
        top_k: int = 100
    ) -> None:
        """Cache search results."""
        if not self._enabled:
            return

        key = QueryNormalizer.search_cache_key(expanded_query, filter_expr, top_k)

        with self._search_lock:
            self._search_cache[key] = results
            logger.debug(f"Search cache SET: {key[:16]}... ({len(results)} results)")

    # =========================================================================
    # Cache Invalidation
    # =========================================================================

    def invalidate_all(self) -> Dict[str, int]:
        """
        Invalidate all caches.

        Call this when policies are updated.

        Returns:
            Dict with counts of invalidated entries per cache
        """
        counts = {}

        with self._expansion_lock:
            counts['expansion'] = len(self._expansion_cache)
            self._expansion_cache.clear()

        with self._response_lock:
            counts['response'] = len(self._response_cache)
            self._response_cache.clear()

        with self._search_lock:
            counts['search'] = len(self._search_cache)
            self._search_cache.clear()

        self._cache_version = datetime.utcnow().isoformat()
        self._invalidation_count += 1

        logger.info(
            f"All caches invalidated. Cleared: "
            f"expansion={counts['expansion']}, "
            f"response={counts['response']}, "
            f"search={counts['search']}. "
            f"New version: {self._cache_version}"
        )

        return counts

    def invalidate_responses(self) -> int:
        """
        Invalidate only response cache.

        Use for accuracy-critical updates when search results may still be valid.

        Returns:
            Number of entries invalidated
        """
        with self._response_lock:
            count = len(self._response_cache)
            self._response_cache.clear()

        logger.info(f"Response cache invalidated: {count} entries cleared")
        return count

    def invalidate_search(self) -> int:
        """
        Invalidate only search cache.

        Use when index configuration changes but responses may still be valid.

        Returns:
            Number of entries invalidated
        """
        with self._search_lock:
            count = len(self._search_cache)
            self._search_cache.clear()

        logger.info(f"Search cache invalidated: {count} entries cleared")
        return count

    # =========================================================================
    # Statistics & Monitoring
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with self._expansion_lock:
            expansion_size = len(self._expansion_cache)
        with self._response_lock:
            response_size = len(self._response_cache)
        with self._search_lock:
            search_size = len(self._search_cache)

        return {
            "enabled": self._enabled,
            "version": self._cache_version,
            "invalidation_count": self._invalidation_count,
            "expansion": {
                "size": expansion_size,
                "max_size": self._expansion_cache.maxsize,
                "hits": self._expansion_stats.hits,
                "misses": self._expansion_stats.misses,
                "hit_rate": f"{self._expansion_stats.hit_rate:.2%}",
                "total_requests": self._expansion_stats.total_requests
            },
            "response": {
                "size": response_size,
                "max_size": self._response_cache.maxsize,
                "ttl_seconds": self._response_cache.ttl,
                "hits": self._response_stats.hits,
                "misses": self._response_stats.misses,
                "hit_rate": f"{self._response_stats.hit_rate:.2%}",
                "total_requests": self._response_stats.total_requests
            },
            "search": {
                "size": search_size,
                "max_size": self._search_cache.maxsize,
                "ttl_seconds": self._search_cache.ttl,
                "hits": self._search_stats.hits,
                "misses": self._search_stats.misses,
                "hit_rate": f"{self._search_stats.hit_rate:.2%}",
                "total_requests": self._search_stats.total_requests
            },
            "memory_estimate_mb": self._estimate_memory_mb()
        }

    def _estimate_memory_mb(self) -> float:
        """Estimate current memory usage in MB."""
        # Rough estimates based on typical object sizes
        with self._expansion_lock:
            expansion_mb = len(self._expansion_cache) * 0.0001  # ~100 bytes each
        with self._response_lock:
            response_mb = len(self._response_cache) * 0.02  # ~20KB each
        with self._search_lock:
            search_mb = len(self._search_cache) * 0.05  # ~50KB each

        return round(expansion_mb + response_mb + search_mb, 2)


# =============================================================================
# Global Singleton
# =============================================================================

_cache_service: Optional[CacheService] = None
_cache_lock = threading.Lock()


def get_cache_service() -> CacheService:
    """Get or create the global cache service instance."""
    global _cache_service

    with _cache_lock:
        if _cache_service is None:
            _cache_service = CacheService()
        return _cache_service


def init_cache_service(
    expansion_cache_size: int = CacheService.DEFAULT_EXPANSION_CACHE_SIZE,
    response_cache_size: int = CacheService.DEFAULT_RESPONSE_CACHE_SIZE,
    search_cache_size: int = CacheService.DEFAULT_SEARCH_CACHE_SIZE,
    response_ttl: int = CacheService.DEFAULT_RESPONSE_TTL,
    search_ttl: int = CacheService.DEFAULT_SEARCH_TTL,
    enabled: bool = True
) -> CacheService:
    """
    Initialize the global cache service with custom settings.

    Should be called during application startup if custom settings are needed.
    """
    global _cache_service

    with _cache_lock:
        _cache_service = CacheService(
            expansion_cache_size=expansion_cache_size,
            response_cache_size=response_cache_size,
            search_cache_size=search_cache_size,
            response_ttl=response_ttl,
            search_ttl=search_ttl,
            enabled=enabled
        )
        return _cache_service


def invalidate_caches() -> Dict[str, int]:
    """
    Invalidate all caches.

    Convenience function for use after policy updates.
    """
    service = get_cache_service()
    return service.invalidate_all()
