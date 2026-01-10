"""
Query processing utilities for the RUSH Policy RAG system.

This module contains functions for:
- Instance search intent detection
- Policy identifier resolution
- Response validation and cleaning
- Query normalization

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
import re
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)

# ============================================================================
# Instance Search Query Patterns - "find X in policy Y" type queries
# ============================================================================
INSTANCE_SEARCH_PATTERNS = [
    # "show me X in policy Y" patterns
    r"show\s+(?:me\s+)?(?:all\s+)?(?:instances?\s+of\s+)?['\"]?(.+?)['\"]?\s+in\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?)(?:\s+policy)?$",
    r"find\s+(?:all\s+)?(?:mentions?\s+of\s+)?['\"]?(.+?)['\"]?\s+in\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?)(?:\s+policy)?$",
    r"where\s+(?:does|is|are)\s+['\"]?(.+?)['\"]?\s+(?:appear|mentioned|discussed|covered)\s+in\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?)$",
    r"search\s+(?:for\s+)?['\"]?(.+?)['\"]?\s+(?:within|in)\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?)$",
    r"locate\s+(?:the\s+)?(?:section\s+(?:about|on|for)\s+)?['\"]?(.+?)['\"]?\s+in\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?)$",
    # "what does policy X say about Y" patterns
    r"what\s+does\s+(?:the\s+)?(.+?)\s+(?:policy\s+)?say\s+about\s+['\"]?(.+?)['\"]?$",
    # "in policy X, find Y" patterns (reversed order)
    r"in\s+(?:the\s+)?(?:policy\s+)?(?:ref\s*#?\s*)?(.+?),?\s+(?:find|show|locate|search\s+for)\s+['\"]?(.+?)['\"]?$",
]

# Common policy name variations for matching
POLICY_NAME_PATTERNS = {
    "hipaa": ["hipaa", "hipaa privacy", "privacy policy", "528"],
    "verbal orders": ["verbal", "verbal orders", "telephone orders", "486"],
    "hand off": ["hand off", "handoff", "hand-off", "communication", "1206"],
    "rapid response": ["rapid response", "rrt", "code blue", "346"],
    "latex": ["latex", "latex management", "228"],
}

# Domain-specific hints for policy resolution
POLICY_HINTS = [
    {
        "keywords": ["verbal order", "telephone order", "verbal orders", "telephone orders",
                    "accept verbal", "accept telephone", "receive verbal", "receive telephone",
                    "authorized to accept", "authorized to receive", "medical assistant",
                    "unit secretary", "nursing aide", "can accept order", "can receive order"],
        "hint": "Verbal and Telephone Orders policy Ref #486",
        "reference": "486",
        "policy_query": "Verbal and Telephone Orders"
    },
    {
        "keywords": ["hand off", "handoff", "sbar", "change of shift"],
        "hint": "Communication Of Patient Status - Hand Off Communication Ref #1206",
        "reference": "1206",
        "policy_query": "Communication Of Patient Status - Hand Off Communication"
    },
    {
        "keywords": ["latex"],
        "hint": "Latex Management policy Ref #228",
        "reference": "228",
        "policy_query": "Latex Management"
    },
    {
        "keywords": ["rapid response", "rrt", "cardiac arrest", "code blue",
                    "emergency number", "call for help", "patient deteriorating",
                    "mews score", "vital signs", "clinical signs"],
        "hint": "Adult Rapid Response policy Ref #346",
        "reference": "346",
        "policy_query": "Adult Rapid Response"
    },
    {
        "keywords": ["informed consent", "consent form", "agree to treatment",
                    "patient agreement", "sign consent", "procedure consent",
                    "surgical consent", "treatment consent", "patient consent",
                    "consent process", "consent documentation"],
        "hint": "Informed Consent policy Ref #275",
        "reference": "275",
        "policy_query": "Informed Consent"
    }
]

# Patterns indicating a "not found" or refusal response
NOT_FOUND_OR_REFUSAL_PATTERNS = [
    "i could not find",
    "couldn't find",
    "could not find",
    "not in rush policies",
    "not in my knowledge",
    "outside my scope",
    "outside the scope",
    "i cannot provide guidance",
    "cannot provide guidance",
    "i only answer rush policy",
    "could you please rephrase",
    "i didn't understand",
    "please clarify",
    "clarify your question",
    "what rush policy topic",
]

# Title normalization rules
TITLE_NORMALIZATION_RULES = [
    (re.compile(r"\\bpyis\\b", re.IGNORECASE), "Pyxis"),
    (re.compile(r"\\bmedstations\\b", re.IGNORECASE), "MedStations"),
]


def detect_instance_search_intent(query: str) -> Optional[Tuple[str, str]]:
    """
    Detect if query is requesting to find specific text/sections within a policy.

    Args:
        query: The user's search query

    Returns:
        Tuple of (search_term, policy_identifier) if detected, None otherwise.
        policy_identifier could be a ref number or policy name.
    """
    query_clean = query.strip().lower()

    for pattern in INSTANCE_SEARCH_PATTERNS:
        match = re.search(pattern, query_clean, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                # Usually first group is search term, second is policy
                search_term = groups[0].strip().strip("'\"")
                policy_id = groups[1].strip().strip("'\"")

                # Clean up policy identifier
                policy_id = re.sub(r'^ref\s*#?\s*', '', policy_id, flags=re.IGNORECASE).strip()
                policy_id = re.sub(r'\s+policy$', '', policy_id, flags=re.IGNORECASE).strip()

                if search_term and policy_id:
                    logger.info(f"Instance search detected: term='{search_term}', policy='{policy_id}'")
                    return (search_term, policy_id)

    return None


def resolve_policy_identifier(policy_id: str) -> Optional[str]:
    """
    Resolve a policy name/description to a reference number.

    Args:
        policy_id: Could be "528", "HIPAA", "HIPAA Privacy Policy", etc.

    Returns:
        Reference number if resolved, original ID otherwise
    """
    policy_id_lower = policy_id.lower().strip()

    # If it's already a reference number (digits only or digits with prefix)
    ref_match = re.match(r'^(?:ref\s*#?\s*)?(\d+(?:\.\d+)?(?:-[a-z]+)?)$', policy_id_lower, re.IGNORECASE)
    if ref_match:
        return ref_match.group(1)

    # Check known policy name patterns
    for canonical, variations in POLICY_NAME_PATTERNS.items():
        for var in variations:
            if var in policy_id_lower:
                # Return the reference number for known policies
                for hint in POLICY_HINTS:
                    if any(kw in canonical for kw in hint["keywords"]):
                        return hint["reference"]

    # Return original ID - it might be a title match
    return policy_id


def strip_references_from_negative_response(response_text: str) -> str:
    """
    Remove any policy references from negative responses (not found, refusal, etc.).

    This ensures responses like "I could not find this. Ref #123..." become
    just "I could not find this in RUSH policies."

    Args:
        response_text: The LLM response text

    Returns:
        Cleaned response text without spurious references
    """
    if not response_text:
        return response_text

    response_lower = response_text.lower()

    # Check if this is a negative response type
    is_negative = any(pattern in response_lower for pattern in NOT_FOUND_OR_REFUSAL_PATTERNS)

    if not is_negative:
        return response_text

    # Strip reference patterns
    # Pattern: "1. Ref #XXX — Title (Section: Y; Applies To: Z)"
    cleaned = re.sub(r'\n*\d+\.\s*Ref\s*#[^\n]+', '', response_text)

    # Pattern: standalone "Ref #XXX" or "(Ref #XXX)"
    cleaned = re.sub(r'\s*\(?Ref\s*#\s*[A-Za-z0-9.\-]+\)?', '', cleaned)

    # Pattern: "Reference Number: XXX"
    cleaned = re.sub(r'\s*Reference\s*Number[:\s]*[A-Za-z0-9.\-]+', '', cleaned, flags=re.IGNORECASE)

    # Clean up multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    return cleaned.strip()


def is_refusal_response(response_text: str) -> bool:
    """
    Detect if the LLM response indicates a refusal, out-of-scope, or not-found case.

    These responses should have their evidence and sources arrays cleared because
    the frontend would otherwise display citations that are misleading.

    Args:
        response_text: The LLM response text

    Returns:
        True if evidence/sources should be cleared
    """
    if not response_text:
        return False

    response_lower = response_text.lower()
    return any(pattern in response_lower for pattern in NOT_FOUND_OR_REFUSAL_PATTERNS)


def truncate_verbatim(text: str, max_chars: int = 3000) -> str:
    """
    Trim long snippets while preserving sentence integrity.

    Args:
        text: The text to truncate
        max_chars: Maximum number of characters

    Returns:
        Truncated text with ellipsis if needed
    """
    if not text:
        return ""

    snippet = text.strip()
    if len(snippet) <= max_chars:
        return snippet

    truncated = snippet[:max_chars]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip() + "…"


def normalize_policy_title(title: Optional[str]) -> str:
    """
    Normalize policy titles by fixing common OCR/PDF extraction errors.

    Args:
        title: The policy title to normalize

    Returns:
        Normalized title string
    """
    if not title:
        return ""

    normalized = title.strip()
    for pattern, replacement in TITLE_NORMALIZATION_RULES:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def get_policy_hint(query: str) -> Optional[dict]:
    """
    Get a policy hint based on query keywords.

    Args:
        query: The user's search query

    Returns:
        Policy hint dict if found, None otherwise
    """
    query_lower = query.lower()
    for hint in POLICY_HINTS:
        if any(kw in query_lower for kw in hint["keywords"]):
            return hint
    return None
