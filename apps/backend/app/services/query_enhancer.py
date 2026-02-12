"""
Query enhancement utilities for the RUSH Policy RAG system.

This module contains functions to enhance, expand, and normalize user queries
before they are processed by the RAG pipeline. It handles:
- Query variant generation for multi-query fusion
- Reciprocal Rank Fusion (RRF) for result merging
- Location context normalization
- Punctuation normalization
- Policy hint application

Extracted from chat_service.py as part of tech debt refactoring.
"""

import re
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app.services.entity_ranking import LOCATION_CONTEXT_PATTERNS
from app.services.query_processor import POLICY_HINTS, get_policy_hint

logger = logging.getLogger(__name__)


# ============================================================================
# Query Variant Generation
# ============================================================================
# Healthcare-specific reformulations for multi-query fusion
QUERY_REFORMULATIONS = {
    'what is': ['define', 'explain', 'describe'],
    'how do i': ['procedure for', 'steps to', 'process for'],
    'when': ['timing for', 'schedule for', 'requirements for'],
    'who can': ['authorization for', 'eligibility for', 'permitted to'],
    'policy for': ['guidelines for', 'protocol for', 'procedure for'],
}


def generate_query_variants(query: str) -> List[str]:
    """
    Generate query variants for multi-query fusion.

    Creates variations of the original query to capture different
    phrasings and terminology. This improves recall by searching
    for multiple interpretations of the user's intent.

    Args:
        query: User's original query

    Returns:
        List of query variants (including original), max 4 variants

    Examples:
        >>> generate_query_variants("what is the hand hygiene policy")
        ['what is the hand hygiene policy', 'define the hand hygiene policy', ...]
    """
    variants = [query]  # Always include original

    query_lower = query.lower()

    # Healthcare-specific reformulations
    for pattern, alternatives in QUERY_REFORMULATIONS.items():
        if pattern in query_lower:
            for alt in alternatives[:2]:  # Max 2 variants per pattern
                variant = query_lower.replace(pattern, alt)
                variants.append(variant)
            break  # Apply only first matching pattern

    # Add keyword-focused variant (removes question words)
    keywords = query_lower.replace('what is', '').replace('how do i', '').replace('when', '').replace('who can', '')
    keywords = ' '.join(keywords.split())  # Normalize whitespace
    if keywords and keywords != query_lower:
        variants.append(keywords)

    logger.debug(f"Query variants for '{query[:30]}...': {len(variants)} variants")
    return variants[:4]  # Cap at 4 variants


# ============================================================================
# Reciprocal Rank Fusion (RRF)
# ============================================================================

def reciprocal_rank_fusion(
    result_lists: List[List[Dict]],
    k: int = 60
) -> List[Dict]:
    """
    Merge multiple result lists using Reciprocal Rank Fusion (RRF).

    RRF is a simple but effective fusion algorithm that combines rankings
    from multiple retrieval methods. Documents appearing in multiple lists
    or at higher ranks get boosted.

    Formula: RRF_score(d) = Σ 1/(k + rank(d))

    Args:
        result_lists: List of result lists, each containing dicts with 'id' or 'reference_number'
        k: Ranking constant (default 60, per original RRF paper)

    Returns:
        Merged list sorted by RRF score (highest first)

    Examples:
        >>> list1 = [{'id': 'A', 'score': 0.9}, {'id': 'B', 'score': 0.8}]
        >>> list2 = [{'id': 'B', 'score': 0.95}, {'id': 'C', 'score': 0.7}]
        >>> merged = reciprocal_rank_fusion([list1, list2])
        >>> merged[0]['id']  # 'B' appears in both lists
        'B'
    """
    if not result_lists:
        return []

    if len(result_lists) == 1:
        return result_lists[0]

    # Calculate RRF scores
    scores = defaultdict(float)
    doc_map = {}  # Store full document data keyed by ID

    for results in result_lists:
        for rank, doc in enumerate(results, start=1):
            # Use reference_number as primary ID, fallback to other identifiers
            doc_id = (
                doc.get('reference_number') or
                doc.get('id') or
                doc.get('title', '')[:50]
            )
            if doc_id:
                scores[doc_id] += 1.0 / (k + rank)
                # Keep the most detailed version of each doc
                if doc_id not in doc_map or len(str(doc)) > len(str(doc_map[doc_id])):
                    doc_map[doc_id] = doc

    # Sort by RRF score (descending)
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    # Return documents in RRF order
    return [doc_map[doc_id] for doc_id in sorted_ids if doc_id in doc_map]


# ============================================================================
# Location Context Normalization
# ============================================================================

def normalize_location_context(query: str) -> Tuple[str, Optional[str]]:
    """
    Normalize generic location context phrases that don't change the core question.

    This helps queries like "What is the hand hygiene policy in a patient room?"
    return results even when the policy doesn't mention "patient room" verbatim,
    since general policies apply to all locations.

    CONSERVATIVE approach: Only strips generic phrases like "in a patient room",
    "at the bedside". Preserves entity names (Oak Park, Copley) and department
    codes (ED, ICU, OR) which may be intentional filters.

    Args:
        query: User's search query

    Returns:
        Tuple of (normalized_query, extracted_context_or_None)

    Examples:
        >>> normalize_location_context("hand hygiene policy in a patient room")
        ('hand hygiene policy', 'in a patient room')

        >>> normalize_location_context("hand hygiene policy at Oak Park")
        ('hand hygiene policy at Oak Park', None)  # Preserves entity name
    """
    original = query
    extracted = []

    for pattern in LOCATION_CONTEXT_PATTERNS:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            extracted.append(match.group().strip())
            query = re.sub(pattern, '', query, flags=re.IGNORECASE)

    # Clean up extra whitespace
    query = ' '.join(query.split())

    # Remove leading/trailing punctuation and spaces (handles leftover commas)
    query = query.strip(' ,;:')

    # Remove space before punctuation (e.g., "policy ?" -> "policy?")
    query = re.sub(r'\s+([?!.,;:])', r'\1', query)

    # Ensure space after punctuation when followed by alphanumeric (not another punctuation).
    # Keep decimal numbers intact (e.g., policy codes "05.00" must NOT become "05. 00").
    query = re.sub(r'([?!,;:])([a-zA-Z0-9])', r'\1 \2', query)
    query = re.sub(r'(?<!\d)\.([a-zA-Z0-9])', r'. \1', query)

    if extracted:
        context = ', '.join(extracted)
        logger.info(f"Normalized location context: '{original}' -> '{query}' (context: {context})")
        return query, context

    return query, None


# ============================================================================
# Punctuation Normalization
# ============================================================================

def normalize_query_punctuation(query: str) -> str:
    """
    Normalize query punctuation for consistent matching.

    Handles:
    - Remove possessives: "RUMC's" -> "RUMC"
    - Normalize smart quotes: "" -> ""
    - Clean up extra whitespace

    This helps queries like "RUMC's NICU" match "RUMC NICU" in the index.

    Args:
        query: User's search query

    Returns:
        Punctuation-normalized query

    Examples:
        >>> normalize_query_punctuation("RUMC's NICU policy")
        'RUMC NICU policy'

        >>> normalize_query_punctuation('"smart quotes"')
        '"smart quotes"'
    """
    # Remove possessives ('s and trailing ')
    normalized = re.sub(r"(\w+)'s\b", r"\1", query)
    normalized = re.sub(r"(\w+)'\b", r"\1", normalized)

    # Normalize smart/curly quotes to standard quotes
    normalized = normalized.replace('\u201c', '"').replace('\u201d', '"')
    normalized = normalized.replace('\u2018', "'").replace('\u2019', "'")

    # Normalize whitespace
    normalized = ' '.join(normalized.split())

    # Strip trailing/leading punctuation that interferes with search matching
    # Preserves mid-query punctuation and question marks (query intent)
    # Must run AFTER whitespace normalization so trailing "value, " → "value," → "value"
    normalized = normalized.strip(',;:.')

    if normalized != query:
        logger.debug(f"Query punctuation normalized: '{query}' -> '{normalized}'")

    return normalized


# ============================================================================
# Policy Hint Application
# ============================================================================

def apply_policy_hints(query: str) -> Tuple[str, List[dict]]:
    """
    Append domain hints and collect target references for deterministic retrieval.

    Policy hints are keyword-triggered expansions that help retrieve specific
    policies for common queries. For example, "verbal order" queries get hints
    to ensure the verbal orders policy (Ref 528) is retrieved.

    Args:
        query: User's search query

    Returns:
        Tuple of (hint_enhanced_query, list_of_matched_policy_entries)

    Examples:
        >>> enhanced, entries = apply_policy_hints("verbal order readback")
        >>> "verbal orders" in enhanced.lower()
        True
        >>> entries[0]['reference']
        '528'
    """
    query_lower = query.lower()
    hints_to_add = []
    forced_entries: List[dict] = []

    for entry in POLICY_HINTS:
        if any(keyword in query_lower for keyword in entry["keywords"]):
            hints_to_add.append(entry["hint"])
            forced_entries.append(entry)

    if hints_to_add:
        return f"{query} {' '.join(hints_to_add)}", forced_entries

    return query, forced_entries


# ============================================================================
# Policy Number Detection
# ============================================================================

# Matches RUSH coded policy numbers: HR-C 05.00, HR-C 05, HR-C05.00, hr-c 05.00
# Also tolerates malformed forms like HR-C 0.600 for normalization.
# Dash is REQUIRED to avoid false positives on natural English (e.g., "do I 2")
POLICY_NUMBER_PATTERN = re.compile(
    r'\b([A-Za-z]{2})-\s*([A-Za-z])\s*(\d{1,2})(?:\.(\d{1,4}))?\b'
)


def _normalize_policy_number_parts(prefix: str, letter: str, major: str, minor: Optional[str]) -> str:
    """
    Normalize policy number components into canonical format: XX-X NN.NN.

    Handles known malformed user variants:
    - HR-C05.00 -> HR-C 05.00
    - HR-C 5.00 -> HR-C 05.00
    - HR-C 0.600 -> HR-C 06.00
    """
    prefix = prefix.upper()
    letter = letter.upper()

    if minor is None:
        number = major.zfill(2)
        sub = "00"
        return f"{prefix}-{letter} {number}.{sub}"

    # Known malformed form where leading zero leaks into major and minor has 3 digits.
    # Example: major=0, minor=600 -> combined 0600 -> 06.00
    if len(minor) == 3 and major == "0":
        digits = f"{major}{minor}".zfill(4)[:4]
        number = digits[:2]
        sub = digits[2:]
        return f"{prefix}-{letter} {number}.{sub}"

    number = major.zfill(2)
    if len(minor) == 1:
        sub = minor + "0"
    else:
        sub = minor[:2]

    return f"{prefix}-{letter} {number}.{sub}"


def detect_policy_number(query: str) -> Optional[Tuple[str, str]]:
    """
    Detect RUSH policy number pattern in a query.

    Returns (normalized_ref, odata_filter) if found, else None.
    Handles variations: HR-C 05.00, HR-C 05, HR-C05.00, hr-c 05.00

    Uses search.ismatch on the title field because many policies store
    the policy number in the title (e.g., "HR-C 05.00 Shift Differentials")
    rather than in the reference_number field.

    Args:
        query: User's search query

    Returns:
        Tuple of (normalized_reference_number, OData_filter_expression) or None
    """
    match = POLICY_NUMBER_PATTERN.search(query)
    if not match:
        return None
    normalized = _normalize_policy_number_parts(
        prefix=match.group(1),
        letter=match.group(2),
        major=match.group(3),
        minor=match.group(4),
    )
    # Prefer exact metadata filter on canonical policy_number.
    # Keep title phrase matching as fallback for pre-migration indexes.
    odata = (
        f"(policy_number eq '{normalized}') or "
        f"(reference_number eq '{normalized}') or "
        f"(search.ismatch('\"{normalized}\"', 'title', 'full', 'all'))"
    )
    logger.info(f"Detected policy number in query: '{query}' -> '{normalized}'")
    return normalized, odata
