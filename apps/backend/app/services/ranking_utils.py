"""
Ranking utilities for the RUSH Policy RAG system.

This module contains functions for:
- Maximal Marginal Relevance (MMR) diversification
- Surge capacity policy penalty handling
- Result re-ranking utilities

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.cohere_rerank_service import RerankResult

logger = logging.getLogger(__name__)


def apply_mmr_diversification(
    citations: List,
    lambda_param: float = 0.7,
    max_results: int = 10
) -> List:
    """
    Apply Maximal Marginal Relevance (MMR) to diversify citations.

    This ensures multi-policy queries return citations from different policies
    rather than multiple chunks from the same policy.

    MMR formula: score = lambda * relevance - (1 - lambda) * similarity

    Args:
        citations: List of citation objects with filepath and reranker_score attributes
        lambda_param: Balance between relevance (1.0) and diversity (0.0). Default 0.7
        max_results: Maximum number of results to return

    Returns:
        Diversified list of citations
    """
    if not citations or len(citations) <= 1:
        return citations

    selected = []
    remaining = list(citations)
    seen_policies = set()  # Track source files to ensure diversity

    while remaining and len(selected) < max_results:
        if not selected:
            # First pick: highest relevance
            selected.append(remaining.pop(0))
            if hasattr(selected[0], 'filepath') and selected[0].filepath:
                seen_policies.add(selected[0].filepath)
            continue

        best_score = -float('inf')
        best_idx = 0

        for i, candidate in enumerate(remaining):
            # Get relevance score
            relevance = getattr(candidate, 'reranker_score', None) or 0.0

            # Calculate similarity penalty (1.0 if same policy, 0.0 if different)
            candidate_policy = getattr(candidate, 'filepath', '') or ''
            similarity = 1.0 if candidate_policy in seen_policies else 0.0

            # MMR score
            mmr_score = lambda_param * relevance - (1 - lambda_param) * similarity

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        best_candidate = remaining.pop(best_idx)
        selected.append(best_candidate)

        if hasattr(best_candidate, 'filepath') and best_candidate.filepath:
            seen_policies.add(best_candidate.filepath)

    return selected


# Keywords that indicate surge level or capacity-based policies
SURGE_KEYWORDS = [
    "surge level",
    "surge capacity",
    "capacity-based",
    "surge protocol",
    "surge plan",
    "capacity surge",
    "surge response",
    "surge activation",
]


def is_surge_capacity_policy(result: 'RerankResult') -> bool:
    """
    Detect if a policy is a surge level or capacity-based policy.

    Checks title and content for keywords indicating surge/capacity policies.
    These policies are rarely used and should be deprioritized in general queries.

    Args:
        result: RerankResult to check

    Returns:
        True if policy appears to be surge/capacity-related
    """
    title_lower = (result.title or "").lower()
    content_lower = (result.content or "").lower()
    text_to_check = f"{title_lower} {content_lower}"

    # Check if any surge keyword appears in title or content
    for keyword in SURGE_KEYWORDS:
        if keyword in text_to_check:
            return True

    return False


def apply_surge_capacity_penalty(
    results: List['RerankResult'],
    penalty: float = 0.6
) -> List['RerankResult']:
    """
    Apply score penalty to surge level/capacity-based policies.

    Demotes these policies in ranking while keeping them in results.
    This prevents rarely-used surge policies from appearing at the top
    for general queries (e.g., "restraint documentation").

    Args:
        results: List of RerankResult objects from Cohere reranking
        penalty: Multiplier to apply to surge policy scores (0.0-1.0)

    Returns:
        List of RerankResult objects with adjusted scores, re-sorted by score
    """
    # Import here to avoid circular import
    from app.services.cohere_rerank_service import RerankResult

    if not results or penalty >= 1.0:
        return results

    adjusted_results = []
    penalized_count = 0

    for result in results:
        if is_surge_capacity_policy(result):
            # Apply penalty to score
            adjusted_score = result.cohere_score * penalty
            # Create new RerankResult with adjusted score
            adjusted_result = RerankResult(
                content=result.content,
                title=result.title,
                reference_number=result.reference_number,
                source_file=result.source_file,
                section=result.section,
                applies_to=result.applies_to,
                cohere_score=adjusted_score,
                original_index=result.original_index
            )
            adjusted_results.append(adjusted_result)
            penalized_count += 1
        else:
            adjusted_results.append(result)

    if penalized_count > 0:
        logger.info(
            f"Applied surge capacity penalty to {penalized_count} policies "
            f"(penalty={penalty}, {penalized_count}/{len(results)} policies affected)"
        )
        # Re-sort by adjusted score (descending)
        adjusted_results.sort(key=lambda r: r.cohere_score, reverse=True)

    return adjusted_results


def apply_mmr_to_rerank_results(
    results: List['RerankResult'],
    lambda_param: float = 0.7,
    max_results: int = 10
) -> List['RerankResult']:
    """
    Apply MMR diversification specifically to RerankResult objects.

    This is a wrapper around apply_mmr_diversification that handles
    RerankResult-specific attributes.

    Args:
        results: List of RerankResult objects
        lambda_param: Balance between relevance and diversity
        max_results: Maximum number of results to return

    Returns:
        Diversified list of RerankResult objects
    """
    if not results or len(results) <= 1:
        return results

    selected = []
    remaining = list(results)
    seen_policies = set()  # Track source files for diversity

    while remaining and len(selected) < max_results:
        if not selected:
            # First pick: highest relevance (already sorted by cohere_score)
            best = remaining.pop(0)
            selected.append(best)
            if best.source_file:
                seen_policies.add(best.source_file)
            continue

        best_score = -float('inf')
        best_idx = 0

        for i, candidate in enumerate(remaining):
            # Get relevance score from Cohere
            relevance = candidate.cohere_score or 0.0

            # Calculate similarity penalty (1.0 if same policy, 0.0 if different)
            similarity = 1.0 if candidate.source_file in seen_policies else 0.0

            # MMR score
            mmr_score = lambda_param * relevance - (1 - lambda_param) * similarity

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        best_candidate = remaining.pop(best_idx)
        selected.append(best_candidate)

        if best_candidate.source_file:
            seen_policies.add(best_candidate.source_file)

    return selected
