"""
Entity and population-based ranking utilities for the RUSH Policy RAG system.

This module contains functions for:
- RUSH entity code extraction (RUMC, RUMG, ROPH, etc.)
- Location-based score boosting
- Pediatric vs adult population ranking
- Entity pattern matching

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
import re
from typing import Dict, List, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.cohere_rerank_service import RerankResult

from app.core.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# RUSH Entity Detection Patterns (Module-level for performance)
# Uses word boundary regex to prevent false positives like "rch" in "search"
# ============================================================================
ENTITY_PATTERNS: Dict[str, List[str]] = {
    'RUMC': [r'\brumc\b', r'\brush university medical center\b', r'\brush medical center\b', r'\brush hospital\b'],
    'RUMG': [r'\brumg\b', r'\brush university medical group\b'],
    'RMG': [r'\brmg\b', r'\brush medical group\b'],
    'ROPH': [r'\broph\b', r'\brush oak park\b', r'\boak park hospital\b', r'\boak park campus\b'],
    'RCMC': [r'\brcmc\b', r'\brush copley\b', r'\bcopley medical center\b', r'\bcopley hospital\b'],
    'RCH': [r'\brch\b', r'\brush children\b', r'\bpediatric hospital\b', r'\bchildrens hospital\b', r"\brush children's\b"],
    'ROPPG': [r'\broppg\b', r'\boak park physicians\b'],
    'RCMG': [r'\brcmg\b', r'\bcopley medical group\b'],
    'RU': [r'\brush university\b'],  # Check last to avoid false positives from 'rush'
}


# ============================================================================
# Location Context Patterns (Module-level for performance)
# CONSERVATIVE: Only generic location phrases that don't specify a RUSH entity
# ============================================================================
LOCATION_CONTEXT_PATTERNS: List[str] = [
    r'\s*\bin\s+(?:a\s+)?patient\s+room(?:s)?\b',           # "in a patient room"
    r'\s*\bat\s+the\s+bedside\b',                           # "at the bedside"
    r'\s*\bduring\s+(?:a\s+)?(?:procedure|visit)\b',        # "during a procedure"
    r'\s*\bon\s+the\s+(?:floor|unit|ward)\b',               # "on the floor/unit"
    r'\s*\bin\s+(?:the\s+)?(?:clinical|hospital)\s+setting\b',  # "in the clinical setting"
    r'\s*\bwhen\s+caring\s+for\s+(?:a\s+)?patient\b',       # "when caring for a patient"
    r'\s*\bwhile\s+(?:treating|seeing)\s+(?:a\s+)?patient\b', # "while treating a patient"
]


# ============================================================================
# Pediatric vs Adult Population-Based Ranking
# Uses word boundary regex to prevent false positives like "teen" in "canteen"
# ============================================================================

# Regex patterns that indicate pediatric patient population (with word boundaries)
PEDIATRIC_KEYWORD_PATTERNS: List[str] = [
    r'\bpediatric\b', r'\bpeds\b', r'\bpediatrics\b', r'\bpaediatric\b',
    r'\bchild\b', r'\bchildren\b', r'\bkids\b', r'\bkid\b',
    r'\binfant\b', r'\binfants\b', r'\bbaby\b', r'\bbabies\b', r'\bnewborn\b', r'\bnewborns\b',
    r'\bneonatal\b', r'\bneonate\b', r'\bneonates\b',
    r'\bnicu\b', r'\bpicu\b',
    r'\btoddler\b', r'\btoddlers\b',
    r'\badolescent\b', r'\badolescents\b', r'\bteen\b', r'\bteenager\b', r'\bteens\b',
    r'\brch\b',  # Rush Children's Hospital code (word boundary prevents "search" match)
    r'\brush children\b', r"\brush children's\b",
]

# Regex patterns in title/filename that indicate pediatric policy
PEDIATRIC_POLICY_TITLE_PATTERNS: List[str] = [
    r'\bpediatric', r'\bpeds-', r'\bnicu\b', r'\bpicu\b', r'\bneonatal\b',
    r'\binfant', r'\bchild', r'\bnewborn', r'\badolescent', r'\bteen\b',
]


def extract_entity_mentions(query: str) -> Set[str]:
    """
    Extract RUSH entity codes mentioned in query.

    Uses word boundary regex matching to prevent false positives
    (e.g., "rch" won't match "search", "research", etc.)

    Handles both codes (RUMC, ROPH) and full names (Rush Oak Park).

    Args:
        query: User's search query

    Returns:
        Set of entity codes (e.g., {'RUMC', 'ROPH'})
    """
    # Input validation
    if not query or not isinstance(query, str):
        return set()

    query_lower = query.lower()
    found_entities: Set[str] = set()

    for entity_code, patterns in ENTITY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, query_lower):
                found_entities.add(entity_code)
                break  # Found this entity, move to next

    return found_entities


def apply_location_boost(
    results: List['RerankResult'],
    query_entities: Set[str],
    boost: float = 1.3
) -> List['RerankResult']:
    """
    Apply score boost to policies matching entity codes in query.

    This prioritizes location-specific policies when the user mentions
    a specific RUSH entity (e.g., "ROPH visitor policy" boosts ROPH policies).

    Args:
        results: List of RerankResult objects from Cohere reranking
        query_entities: Set of entity codes extracted from query
        boost: Multiplier to apply (>1.0 = boost)

    Returns:
        List with adjusted scores, re-sorted by score
    """
    # Import here to avoid circular import
    from app.services.cohere_rerank_service import RerankResult

    if not results or not query_entities or boost <= 1.0:
        return results

    adjusted_results = []
    boosted_count = 0

    for result in results:
        # Parse applies_to string (e.g., "RUMC, RUMG, ROPH")
        policy_entities = {e.strip().upper() for e in (result.applies_to or "").split(",") if e.strip()}

        # Check if any query entity matches policy entities
        if query_entities & policy_entities:  # Set intersection
            adjusted_score = min(result.cohere_score * boost, 1.0)  # Cap at 1.0
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
            boosted_count += 1
        else:
            adjusted_results.append(result)

    if boosted_count > 0:
        logger.info(
            f"Applied location boost to {boosted_count} policies "
            f"(boost={boost}, entities={query_entities}, "
            f"{boosted_count}/{len(results)} policies boosted)"
        )
        # Re-sort by adjusted score (descending)
        adjusted_results.sort(key=lambda r: r.cohere_score, reverse=True)

    return adjusted_results


def detect_pediatric_context(query: str) -> bool:
    """
    Detect if query mentions pediatric population.

    Returns True if query contains pediatric keywords like "peds", "kids",
    "NICU", "pediatric", "children", "teen", etc.

    Uses word boundary regex matching to prevent false positives like
    "teen" matching "canteen" or "rch" matching "search".

    Args:
        query: User's search query

    Returns:
        True if pediatric context detected, False otherwise (assume adult)
    """
    # Input validation
    if not query or not isinstance(query, str):
        return False

    query_lower = query.lower()
    return any(re.search(pattern, query_lower) for pattern in PEDIATRIC_KEYWORD_PATTERNS)


def is_pediatric_policy(result: 'RerankResult') -> bool:
    """
    Detect if a policy is pediatric-specific by title/filename patterns.

    Checks for patterns like "pediatric-*", "*-nicu-*", "child restraint", etc.
    Uses word boundary regex for accurate matching.

    Args:
        result: RerankResult object from Cohere reranking

    Returns:
        True if policy appears to be pediatric-specific
    """
    title = (result.title or "").lower()
    source = (result.source_file or "").lower()
    combined = f"{title} {source}"

    for pattern in PEDIATRIC_POLICY_TITLE_PATTERNS:
        if re.search(pattern, combined):
            return True
    return False


def apply_population_ranking(
    results: List['RerankResult'],
    is_pediatric_query: bool,
    pediatric_boost: float = None,
    adult_default_boost: float = None,
    adult_penalty_in_peds: float = None,
    peds_penalty_in_adult: float = None
) -> List['RerankResult']:
    """
    Apply score adjustments based on patient population context.

    DEFAULT (no kid words): Boost adult/general policies, penalize pediatric.
    PEDIATRIC QUERY: Boost pediatric policies, penalize adult-only.

    This ensures clinical queries default to adult context (most common)
    while properly prioritizing pediatric policies when explicitly requested.

    Args:
        results: List of RerankResult objects from Cohere reranking
        is_pediatric_query: True if query contains pediatric keywords
        pediatric_boost: Multiplier for pediatric policies when peds query
        adult_default_boost: Multiplier for adult/general when no peds keywords
        adult_penalty_in_peds: Penalty for adult policies in pediatric context
        peds_penalty_in_adult: Penalty for peds policies in adult context

    Returns:
        List with adjusted scores, re-sorted by score
    """
    # Import here to avoid circular import
    from app.services.cohere_rerank_service import RerankResult

    if not results:
        return results

    # Use config values if not explicitly provided
    if pediatric_boost is None:
        pediatric_boost = settings.PEDIATRIC_BOOST
    if adult_default_boost is None:
        adult_default_boost = settings.ADULT_DEFAULT_BOOST
    if adult_penalty_in_peds is None:
        adult_penalty_in_peds = settings.ADULT_PENALTY_IN_PEDS_CONTEXT
    if peds_penalty_in_adult is None:
        peds_penalty_in_adult = settings.PEDS_PENALTY_IN_ADULT_CONTEXT

    adjusted_results = []
    peds_boosted = 0
    adult_boosted = 0

    for result in results:
        is_peds_policy = is_pediatric_policy(result)

        if is_pediatric_query:
            # User mentioned kids/peds: boost pediatric policies
            if is_peds_policy:
                adjusted_score = min(result.cohere_score * pediatric_boost, 1.0)
                peds_boosted += 1
            else:
                # Slight penalty to adult policies when pediatric context
                adjusted_score = result.cohere_score * adult_penalty_in_peds
        else:
            # DEFAULT: Assume adult clinical context
            if is_peds_policy:
                # Penalty to pediatric policies when no pediatric keywords
                adjusted_score = result.cohere_score * peds_penalty_in_adult
            else:
                # Boost adult/general policies (most queries are for adults)
                adjusted_score = min(result.cohere_score * adult_default_boost, 1.0)
                adult_boosted += 1

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

    # Log the adjustments
    if is_pediatric_query:
        logger.info(
            f"Population ranking: pediatric context detected, "
            f"boosted {peds_boosted} pediatric policies (boost={pediatric_boost})"
        )
    else:
        logger.info(
            f"Population ranking: adult default context, "
            f"boosted {adult_boosted} adult/general policies (boost={adult_default_boost})"
        )

    # Count pediatric policies in top results for visibility
    peds_in_top5 = sum(1 for r in adjusted_results[:5] if is_pediatric_policy(r))
    logger.info(f"Pediatric policies in top 5: {peds_in_top5}")

    # Re-sort by adjusted score (descending)
    adjusted_results.sort(key=lambda r: r.cohere_score, reverse=True)

    return adjusted_results


def get_all_entity_codes() -> List[str]:
    """
    Get all supported RUSH entity codes.

    Returns:
        List of entity codes (RUMC, RUMG, RMG, ROPH, RCMC, RCH, ROPPG, RCMG, RU)
    """
    return list(ENTITY_PATTERNS.keys())


def is_entity_specific_query(query: str) -> bool:
    """
    Check if a query mentions any specific RUSH entity.

    Args:
        query: User's search query

    Returns:
        True if query mentions at least one RUSH entity
    """
    entities = extract_entity_mentions(query)
    return len(entities) > 0
