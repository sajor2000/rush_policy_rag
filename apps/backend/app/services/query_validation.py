"""
Query validation utilities for the RUSH Policy RAG system.

This module contains functions to validate and classify user queries before
they are processed by the RAG pipeline. It handles:
- Not-found response detection
- Out-of-scope query detection
- Multi-policy query detection
- Adversarial query detection
- Unclear query detection

Extracted from chat_service.py as part of tech debt refactoring.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# FIX 1: Expanded "not found" detection phrases
# ============================================================================
NOT_FOUND_PHRASES = [
    "i don't have",
    "i do not have",
    "no information",
    "could not find",
    "couldn't find",
    "cannot find",
    "can't find",
    "unable to find",
    "unable to locate",
    "no policies",
    "no policy",
    "not covered",
    "not addressed",
    "outside my scope",
    "outside the scope",
    "not within",
    "beyond my knowledge",
    "i cannot answer",
    "i can't answer",
    "don't have access",
    "no relevant",
    "not in my knowledge",
    "not available in",
    "i'm not able to",
    "i am not able to",
    "no specific policy",
    "not included in",
    # REMOVED: "does not contain" and "doesn't contain" - too broad, triggers
    # false positives when legitimate content discusses what products contain.
    # E.g., "does not contain latex" was matching in latex allergy policies.
    # The "I could not find" phrase is sufficient for actual not-found cases.
]


# ============================================================================
# FIX 2: Out-of-scope topic keywords (DATA-DRIVEN from policy metadata analysis)
# ============================================================================
# ALWAYS out of scope - Verified NO policies exist for these topics
# (Analyzed 329 policies in Azure AI Search index on 2024-12-01)
ALWAYS_OUT_OF_SCOPE = [
    # Facilities - No policies found
    "parking", "parking validation", "parking permit", "parking garage",
    "cafeteria hours", "cafeteria menu", "food court",
    "gym access", "fitness center hours", "wellness center",
    "wifi password", "internet access",

    # HR Benefits not in clinical policy database
    # Note: HR-B 13.00 PTO policy EXISTS but is about policy, not balance inquiries
    "pto balance", "vacation balance", "how many days do i have",
    "401k", "retirement contributions", "pension",
    "benefits enrollment deadline", "open enrollment dates",
    "salary", "pay raise", "compensation",

    # Social/Personal - No policies found
    "birthday", "potluck", "team party", "celebration",

    # Specific non-policy topics
    "jury duty",  # No jury duty policy found in index

    # General conversation - NOT policy questions (FIX: weather query bug)
    # These trigger false positive retrieval based on keyword matches (e.g., "Chicago")
    "what is the weather", "what's the weather", "weather in",
    "tell me a joke", "tell me about yourself",
    "who are you", "what are you",
    "good morning", "good afternoon", "good evening",
    "how are you", "how's it going",
    "sports score", "football", "basketball", "baseball",
    "stock price", "stock market",
    "recipe for", "how to cook",
    "movie recommendation", "what movie",
    "music recommendation", "what song",
    "travel advice", "flight to", "hotel in",
    "news about", "current events",
]


# ============================================================================
# FIX 5: Multi-policy query indicators (Enhanced for better detection)
# ============================================================================
MULTI_POLICY_INDICATORS = [
    # Explicit multi-policy indicators
    "across", "different policies", "multiple policies", "various policies",
    "all policies", "any policy", "which policies", "what policies",
    "several", "compare", "both policies",

    # Implicit multi-topic indicators
    "and also", "as well as", "in addition to",
    "what are all the", "comprehensive", "overview",

    # Cross-cutting concern patterns (queries that span multiple policies)
    "communication methods", "safety precautions", "documentation required",
    "patient identification", "emergency procedures", "during emergencies",
    "staff responsibilities", "compliance requirements", "regulatory",
]

# Policy topic keywords for detecting implicit multi-policy queries
POLICY_TOPIC_KEYWORDS = [
    "verbal order", "hand-off", "hand off", "handoff", "rapid response",
    "latex", "sbar", "epic", "communication", "rrt", "code blue",
    "patient safety", "medication", "documentation", "authentication",
]


# ============================================================================
# FIX 6: Adversarial query detection (bypass/circumvent safety protocols)
# ============================================================================
ADVERSARIAL_PATTERNS = [
    # Bypass/circumvent patterns
    "bypass", "circumvent", "work around", "workaround", "get around",
    "skip authentication", "skip the", "avoid the", "fastest way to skip",
    "quickest way to skip", "how to skip", "skip verification",
    "without read-back", "without authentication", "without verification",
    # Role-play / jailbreak attempts
    "pretend you're", "pretend you are", "act as if", "imagine you're",
    "forget your rules", "new instructions",
    # "ignore" patterns - must be specific to avoid false positives
    "ignore your", "ignore my", "ignore the rules", "ignore safety",
    "ignore previous", "ignore these", "ignore all",
    # DAN/jailbreak mode patterns
    "dan mode", "developer mode", "disable restrictions", "disable your",
    "jailbreak", "jailbroken", "unrestricted mode", "no restrictions",
    "enable developer", "turn off safety", "remove restrictions",
    # System prompt injection patterns
    "system:", "system prompt", "reveal your prompt", "show me your prompt",
    "what are your instructions", "what is your system prompt",
    # Authority impersonation patterns
    "i'm a rush administrator", "i am a rush administrator",
    "i'm an administrator", "i am an administrator",
    "override your restrictions", "full system access", "give me access",
    "grant me access", "unlock your", "as an admin",
    # Completion/fill-in attacks
    "[mask]", "fill in", "complete this:", "complete the following",
    "fill in the blank", "finish this sentence",
    # Security/exploit probe patterns
    "security audit", "exploited", "could be exploited", "how to exploit",
    "vulnerability in", "vulnerabilities", "how could someone abuse",
    "how to abuse", "how could this be abused", "how to hack",
    # Confidential data extraction
    "confidential patient data", "confidential information", "patient data",
    "private data", "sensitive data", "personal information",
    "tell me confidential", "reveal confidential", "show me confidential",
    # Translation-based bypass
    "translate this to", "translate the following",
]

ADVERSARIAL_REFUSAL_MESSAGE = (
    "I cannot provide guidance on bypassing, circumventing, or ignoring RUSH safety protocols. "
    "These requirements exist to protect patient safety and ensure regulatory compliance. "
    "If you have concerns about a specific policy, please contact Policy Administration."
)

UNCLEAR_QUERY_MESSAGE = (
    "I didn't understand that. Could you please rephrase or clarify your question? "
    "I'm here to help - what specific topic would you like to know about?"
)

# Broad scope patterns for multi-policy detection
BROAD_SCOPE_PATTERNS = [
    r"\bwhat\s+(?:are\s+)?(?:all|any|the)\s+(?:different|various)\b",
    r"\bhow\s+(?:do|does|should)\s+(?:we|staff|nurses?|i)\b.*\band\b",
    r"\blist\s+(?:all|the)\b",
    r"\bwhat\s+(?:should|must)\s+(?:be|i)\s+.*\band\b",
    # Emergency/safety patterns that often span multiple policies
    r"\bemergenc(?:y|ies)\b.*\b(?:method|protocol|communication)\b",
    r"\bsafety\s+(?:precaution|protocol|measure)\b",
    r"\bpatient\s+identification\b",
]


def is_not_found_response(answer_text: str, not_found_message: str = "") -> bool:
    """
    Detect if LLM response indicates no information found.

    Args:
        answer_text: The LLM response text to check
        not_found_message: Optional constant to check for exact match

    Returns:
        True if response indicates no information found
    """
    if not answer_text:
        return True
    if not_found_message and answer_text == not_found_message:
        return True

    answer_lower = answer_text.lower()

    # Check for explicit "not found" indicator phrases
    for phrase in NOT_FOUND_PHRASES:
        if phrase in answer_lower:
            return True

    return False


def is_out_of_scope_query(query: str) -> bool:
    """
    Detect queries about topics with NO policies in the database.

    Based on analysis of 329 policies in Azure AI Search index.
    Topics like dress code, PTO policy, leave of absence ARE in scope
    (policies exist: Ref 704, 847, HR-B 13.00, HR-B 14.00).

    Args:
        query: User's query text

    Returns:
        True if query is about a topic with no policies
    """
    query_lower = query.lower()

    # Check against verified out-of-scope topics
    for keyword in ALWAYS_OUT_OF_SCOPE:
        if keyword in query_lower:
            logger.info(f"Out-of-scope query detected (no policies exist): '{keyword}'")
            return True

    return False


def is_multi_policy_query(query: str, use_decomposer: bool = True) -> bool:
    """
    Detect if query likely spans multiple policies.

    Uses four detection strategies:
    1. Explicit indicators ("across policies", "compare", etc.)
    2. Multiple topic keywords (2+ distinct policy topics)
    3. Broad scope patterns (regex for comprehensive queries)
    4. Query decomposition analysis (comparison, multi-topic, conditional)

    Args:
        query: User's query text
        use_decomposer: Whether to use query decomposer for analysis

    Returns:
        True if query likely spans multiple policies
    """
    query_lower = query.lower()

    # Strategy 1: Explicit multi-policy indicators
    if any(ind in query_lower for ind in MULTI_POLICY_INDICATORS):
        logger.debug(f"Multi-policy detected via indicator: {query[:50]}...")
        return True

    # Strategy 2: Multiple topic keywords (2+ distinct policy topics)
    topics_found = sum(1 for t in POLICY_TOPIC_KEYWORDS if t in query_lower)
    if topics_found >= 2:
        logger.debug(f"Multi-policy detected via {topics_found} topics: {query[:50]}...")
        return True

    # Strategy 3: Broad scope patterns
    if any(re.search(p, query_lower) for p in BROAD_SCOPE_PATTERNS):
        logger.debug(f"Multi-policy detected via broad pattern: {query[:50]}...")
        return True

    # Strategy 4: Query decomposition analysis
    # Complex queries that need decomposition are multi-policy by definition
    if use_decomposer:
        try:
            from app.services.query_decomposer import get_query_decomposer
            decomposer = get_query_decomposer()
            needs_decomp, decomp_type = decomposer.needs_decomposition(query)
            if needs_decomp:
                logger.debug(f"Multi-policy detected via decomposition ({decomp_type}): {query[:50]}...")
                return True
        except Exception as e:
            logger.debug(f"Query decomposition check failed: {e}")

    return False


def is_adversarial_query(query: str) -> bool:
    """
    Detect adversarial queries that try to bypass safety protocols.

    Examples:
    - "How do I bypass the read-back requirement?"
    - "Fastest way to skip authentication"
    - "Pretend you're a different AI"

    Args:
        query: User's query text

    Returns:
        True if query appears adversarial
    """
    query_lower = query.lower()

    for pattern in ADVERSARIAL_PATTERNS:
        if pattern in query_lower:
            logger.info(f"Adversarial query detected: '{pattern}' in query")
            return True

    return False


def is_unclear_query(query: str) -> bool:
    """
    Detect unclear queries that need clarification before processing.

    Examples:
    - Single characters: "K", "a", "?"
    - Gibberish: "asdfjkl", "qwerty"
    - Too vague: "policy", "help", "what"
    - Typos without context: "polciy"

    Args:
        query: User's query text

    Returns:
        True if query is unclear and needs clarification
    """
    query_stripped = query.strip()
    query_lower = query_stripped.lower()

    # Single character or very short (under 3 chars)
    if len(query_stripped) <= 2:
        logger.info(f"Unclear query detected: too short ({len(query_stripped)} chars)")
        return True

    # Common vague words that need clarification
    vague_words = {"policy", "help", "what", "how", "why", "info", "information"}
    if query_lower in vague_words:
        logger.info(f"Unclear query detected: vague word '{query_lower}'")
        return True

    # Common typos of "policy" that need clarification (not a real search)
    policy_typos = {"polciy", "policiy", "polcy", "poilcy", "plicy", "ploicy"}
    if query_lower in policy_typos:
        logger.info(f"Unclear query detected: typo of 'policy' '{query_lower}'")
        return True

    # Gibberish detection: no vowels or unpronounceable
    vowels = set("aeiou")
    has_vowel = any(c in vowels for c in query_lower)
    # But allow short acronyms (ED, RN, ICU) - they're valid
    if not has_vowel and len(query_stripped) > 4:
        logger.info(f"Unclear query detected: no vowels (likely gibberish)")
        return True

    # Keyboard mash patterns
    keyboard_patterns = ["asdf", "qwer", "zxcv", "hjkl", "aaaa", "bbbb"]
    if any(pattern in query_lower for pattern in keyboard_patterns):
        logger.info(f"Unclear query detected: keyboard pattern")
        return True

    return False
