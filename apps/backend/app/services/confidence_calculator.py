"""
Confidence scoring utilities for the RUSH Policy RAG system.

This module contains functions for calculating response confidence and
determining when to return "not found" responses. In healthcare settings,
it's critical to avoid providing low-confidence answers that might mislead
clinical decision-making.

Key Features:
- Score window filtering for noise reduction
- Multi-signal confidence calculation
- Grounding-based confidence boosting
- Healthcare-safe routing to "not found"

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.cohere_rerank_service import RerankResult
    from app.models.schemas import EvidenceItem
    from app.services.citation_verifier import VerificationResult

logger = logging.getLogger(__name__)


# ============================================================================
# Score Window Filtering
# ============================================================================

def filter_by_score_window(
    reranked: List["RerankResult"],
    query: str,
    window_threshold: float = 0.6
) -> List["RerankResult"]:
    """
    Filter reranked results to keep only docs within a relative score window.

    For single-intent queries (e.g., "IV dwell time"), we want to keep only
    results that are semantically similar to the top hit. This prevents noise
    from related-but-different policies (e.g., PICC lines when user asked about PIV).

    Args:
        reranked: Cohere reranked results (sorted by score)
        query: Original query (for logging)
        window_threshold: Keep docs with score >= (top_score * threshold)

    Returns:
        Filtered list of reranked results

    Examples:
        >>> # Top result has score 0.9, threshold 0.6 -> keep scores >= 0.54
        >>> results = [RerankResult(score=0.9), RerankResult(score=0.7), RerankResult(score=0.4)]
        >>> filtered = filter_by_score_window(results, "IV dwell time")
        >>> len(filtered)  # Only first two pass threshold
        2
    """
    if not reranked or len(reranked) <= 2:
        return reranked  # Too few results, don't filter

    top_score = reranked[0].cohere_score
    if top_score < 0.3:
        # Low confidence overall - don't filter (might remove valid results)
        logger.info(f"Top score {top_score:.2f} < 0.3, skipping score windowing")
        return reranked

    # Calculate score window threshold
    min_score = top_score * window_threshold

    # Filter results
    filtered = [r for r in reranked if r.cohere_score >= min_score]

    # Ensure we keep at least 2 results (prevent over-filtering)
    if len(filtered) < 2 and len(reranked) >= 2:
        logger.warning(
            f"Score windowing would reduce to {len(filtered)} results, "
            f"keeping top 2 instead"
        )
        return reranked[:2]

    logger.info(
        f"Score windowing: {len(reranked)} → {len(filtered)} results "
        f"(threshold: {min_score:.2f}, top: {top_score:.2f})"
    )
    return filtered


# ============================================================================
# Confidence Calculation
# ============================================================================

def calculate_response_confidence(
    reranked: List["RerankResult"],
    has_evidence: bool = True
) -> Tuple[float, str]:
    """
    Calculate confidence score for healthcare response routing.

    In high-risk healthcare environments, low-confidence responses should
    be routed to "I could not find" rather than risking hallucination.

    Args:
        reranked: List of reranked results from Cohere
        has_evidence: Whether evidence was found

    Returns:
        Tuple of (confidence_score 0.0-1.0, confidence_level "high"|"medium"|"low")

    Examples:
        >>> # High confidence: top score > 0.7 with clear gap
        >>> results = [RerankResult(score=0.85), RerankResult(score=0.5)]
        >>> score, level = calculate_response_confidence(results, True)
        >>> level
        'high'

        >>> # Low confidence: no results
        >>> score, level = calculate_response_confidence([], False)
        >>> level
        'low'
    """
    if not reranked or not has_evidence:
        return 0.0, "low"

    top_score = reranked[0].cohere_score

    # Calculate score gap between top and second result
    score_gap = 0.0
    if len(reranked) > 1:
        score_gap = top_score - reranked[1].cohere_score

    # High confidence: top score > 0.7 AND clear separation from #2
    if top_score > 0.7 and score_gap > 0.15:
        return min(top_score * 1.1, 1.0), "high"

    # Medium-high confidence
    if top_score > 0.5:
        return top_score, "medium"

    # Low-medium confidence
    if top_score > 0.3:
        return top_score * 0.9, "low"

    # Very low confidence
    return top_score * 0.5, "low"


def confidence_level_from_score(score: float) -> str:
    """
    Map a numeric confidence score to qualitative buckets.

    Args:
        score: Numeric confidence score (0.0 to 1.0)

    Returns:
        Qualitative level: "high", "medium", or "low"
    """
    if score >= 0.7:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def boost_confidence_with_grounding(
    confidence_score: float,
    evidence_items: List["EvidenceItem"],
    verification: Optional["VerificationResult"] = None
) -> float:
    """
    Boost confidence using grounding signals per Cohere/AWS guidance.

    When initial confidence is low but we have strong grounding signals
    (verified citations, multiple evidence items), we can boost confidence.

    Args:
        confidence_score: Initial confidence score
        evidence_items: List of grounded evidence items
        verification: Optional citation verification result

    Returns:
        Boosted confidence score (capped at 0.95)

    Examples:
        >>> boost_confidence_with_grounding(0.4, [evidence1, evidence2])
        0.55  # Boosted due to multiple evidence items
    """
    if confidence_score >= 0.5:
        return confidence_score
    if not evidence_items:
        return confidence_score

    boosted = confidence_score

    # Multi-signal scoring: use verifier confidence if available (AWS/Cohere best practice)
    if verification:
        boosted = max(boosted, verification.confidence_score)

    # Additional lift when we have multiple grounded citations
    if len(evidence_items) >= 2:
        boosted = max(boosted, 0.55)
    elif len(evidence_items) == 1:
        boosted = max(boosted, 0.5)

    return min(boosted, 0.95)


def should_return_not_found(
    confidence_score: float,
    confidence_level: str,
    has_evidence: bool
) -> bool:
    """
    Determine if response should be "not found" based on confidence.

    In healthcare, it's better to say "I don't know" than to
    risk providing inaccurate information.

    Args:
        confidence_score: Numeric confidence score
        confidence_level: Qualitative confidence level
        has_evidence: Whether any evidence was found

    Returns:
        True if response should be routed to "not found"
    """
    # No evidence = definitely not found
    if not has_evidence:
        return True

    # Very low confidence = safer to say not found
    if confidence_score < 0.25:
        logger.info(f"Routing to NOT_FOUND: confidence {confidence_score:.2f} too low")
        return True

    return False
