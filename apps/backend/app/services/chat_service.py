import logging
import asyncio
from typing import Optional, List, Dict, Tuple
from collections import defaultdict
from app.models.schemas import ChatRequest, ChatResponse, EvidenceItem
from app.core.prompts import RISEN_PROMPT, NOT_FOUND_MESSAGE, LLM_UNAVAILABLE_MESSAGE
from app.core.security import build_applies_to_filter
from azure_policy_index import PolicySearchIndex, format_rag_context, SearchResult
from app.services.on_your_data_service import OnYourDataService, OnYourDataResult
from app.services.cohere_rerank_service import CohereRerankService, RerankResult
from app.services.synonym_service import get_synonym_service, QueryExpansion
from app.services.citation_verifier import get_citation_verifier, CitationVerifier, VerificationResult
from app.services.citation_formatter import get_citation_formatter
from app.services.safety_validator import get_safety_validator, ResponseSafetyValidator
from app.services.corrective_rag import get_corrective_rag_service, CorrectiveRAGService
from app.services.self_reflective_rag import get_self_reflective_service, SelfReflectiveRAGService
from app.services.query_decomposer import get_query_decomposer, QueryDecomposer
from app.core.config import settings
from openai import AzureOpenAI
import httpx
import os

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

# Topics where policies MAY exist but context matters
# These will pass through to LLM for nuanced handling
# Examples found: Dress code (Ref 704, 847), PTO (HR-B 13.00), Leave (HR-B 14.00)
# REMOVED: dress code, vacation, time off, leave - policies exist for these!

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

# Domain-specific hints to bias retrieval toward canonical policies when
# critical terminology appears in the query (e.g., "verbal orders" → Ref #486)
POLICY_HINTS = [
    {
        "keywords": ["verbal order", "telephone order", "verbal orders", "telephone orders"],
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
    }
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

# Patterns indicating a "not found" or refusal response that should NOT have references
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


def _strip_references_from_negative_response(response_text: str) -> str:
    """
    Remove any policy references from negative responses (not found, refusal, etc.).

    This ensures responses like "I could not find this. Ref #123..." become
    just "I could not find this in RUSH policies."
    """
    import re

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


def _is_refusal_response(response_text: str) -> bool:
    """
    Detect if the LLM response indicates a refusal, out-of-scope, or not-found case.

    These responses should have their evidence and sources arrays cleared because
    the frontend would otherwise display citations that are misleading
    (e.g., showing "Chicago" matches for a weather query).

    Returns True if evidence/sources should be cleared.
    """
    if not response_text:
        return False

    response_lower = response_text.lower()

    # Check for refusal/out-of-scope patterns
    return any(pattern in response_lower for pattern in NOT_FOUND_OR_REFUSAL_PATTERNS)


def _truncate_verbatim(text: str, max_chars: int = 3000) -> str:
    """Trim long snippets while preserving sentence integrity."""
    if not text:
        return ""

    snippet = text.strip()
    if len(snippet) <= max_chars:
        return snippet

    truncated = snippet[:max_chars]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip() + "…"


def _apply_mmr_diversification(
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


def _apply_mmr_to_rerank_results(
    results: List['RerankResult'],
    lambda_param: float = 0.6,
    max_results: int = 10
) -> List['RerankResult']:
    """
    Apply Maximal Marginal Relevance (MMR) to Cohere rerank results.
    
    Ensures multi-policy queries return results from different policies
    rather than multiple chunks from the same policy.
    
    Uses reference_number as the policy identifier for diversity.
    
    Args:
        results: List of RerankResult objects from Cohere reranking
        lambda_param: Balance between relevance (1.0) and diversity (0.0). 
                      Default 0.6 (60% relevance, 40% diversity)
        max_results: Maximum number of results to return
    
    Returns:
        Diversified list of RerankResult objects
    """
    if not results or len(results) <= 1:
        return results
    
    selected = []
    remaining = list(results)
    seen_policies = set()  # Track reference numbers to ensure diversity
    
    while remaining and len(selected) < max_results:
        if not selected:
            # First pick: highest relevance (already sorted by Cohere score)
            first = remaining.pop(0)
            selected.append(first)
            if first.reference_number:
                seen_policies.add(first.reference_number)
            continue
        
        best_score = -float('inf')
        best_idx = 0
        
        for i, candidate in enumerate(remaining):
            # Get relevance score from Cohere
            relevance = candidate.cohere_score or 0.0
            
            # Calculate similarity penalty (1.0 if same policy, 0.0 if different)
            similarity = 1.0 if candidate.reference_number in seen_policies else 0.0
            
            # MMR score: balance relevance with diversity
            mmr_score = lambda_param * relevance - (1 - lambda_param) * similarity
            
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        
        best_candidate = remaining.pop(best_idx)
        selected.append(best_candidate)
        
        if best_candidate.reference_number:
            seen_policies.add(best_candidate.reference_number)
    
    logger.debug(f"MMR diversification: {len(results)} -> {len(selected)} results, {len(seen_policies)} unique policies")
    return selected


def _extract_reference_identifier(citation: str) -> str:
    """Best-effort extraction of reference number from citation text."""
    if not citation or "(" not in citation or ")" not in citation:
        return ""

    try:
        inner = citation.split("(", 1)[1].split(")", 1)[0]
        return inner.replace("Ref:", "").strip()
    except (IndexError, ValueError):
        return ""


def _derive_source_file(title: str, reference_number: str) -> str:
    """Derive a plausible source_file from title/reference when missing."""
    if reference_number:
        return f"{reference_number.lower().replace(' ', '-')}.pdf"
    if title:
        slug = title.lower().replace(" ", "-").replace("/", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        return f"{slug[:80]}.pdf"
    return ""


def _extract_quick_answer(response_text: str) -> str:
    """
    Extract just the quick answer portion from a RISEN-formatted response.

    Strips out:
    - QUICK ANSWER header
    - POLICY REFERENCE section with ASCII box
    - NOTICE footer
    - Citation lines at the end of quick answer

    Returns clean prose suitable for display in the Quick Answer UI box.
    """
    import re

    if not response_text:
        return ""

    text = response_text.strip()

    # If the response is already short (no formatting), return as-is
    if "POLICY REFERENCE" not in text and "┌─" not in text:
        # Still strip the quick answer header if present
        text = re.sub(r'^📋\s*QUICK ANSWER\s*\n*', '', text, flags=re.IGNORECASE)
        return text.strip()

    # Extract text between "QUICK ANSWER" and "POLICY REFERENCE"
    quick_answer_match = re.search(
        r'📋\s*QUICK ANSWER\s*\n+(.*?)(?=📄\s*POLICY REFERENCE|\n*┌─|$)',
        text,
        re.DOTALL | re.IGNORECASE
    )

    if quick_answer_match:
        quick_answer = quick_answer_match.group(1).strip()
    else:
        # Fallback: take everything before the policy reference box
        box_start = text.find('┌─')
        if box_start > 0:
            quick_answer = text[:box_start].strip()
        else:
            # No box found, try to remove just the notice
            notice_match = re.search(r'⚠️\s*NOTICE:', text)
            if notice_match:
                quick_answer = text[:notice_match.start()].strip()
            else:
                quick_answer = text

    # Remove "[Citation: ...]" line at the end (we show this in evidence cards)
    quick_answer = re.sub(
        r'\n*\[Citation:[^\]]+\]',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove "[Policy Name, Ref #XXX]" citation format
    quick_answer = re.sub(
        r'\s*\[[^\]]*Ref\s*#[^\]]+\][,.]?',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove "[Reference Number: XXX]" citation format
    quick_answer = re.sub(
        r'\s*\[Reference\s*Number:[^\]]+\][,.]?',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove "[Policy Name, Reference Number: X.X.X]" format
    quick_answer = re.sub(
        r'\s*\[[^\]]*Reference\s*Number:[^\]]+\][,.]?',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove any remaining bracketed citations at end (catch-all)
    quick_answer = re.sub(
        r'\s*,?\s*\[[^\]]{10,}\][,.]?\s*$',
        '',
        quick_answer
    ).strip()

    # Remove trailing "Applies To: SITE." patterns
    quick_answer = re.sub(
        r',?\s*Applies\s*To:\s*[\w,\s\.]+$',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove standalone "Citation:" line format too
    quick_answer = re.sub(
        r'\n*Citation:\s*[^\n]+$',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove trailing checkbox-style "Applies To:" lines (with checkboxes)
    quick_answer = re.sub(
        r'\.?\s*Applies\s*To:\s*[☒☐\s\w,\.]+$',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove "—applies to SITE" or "applies to SITE" at end
    quick_answer = re.sub(
        r'[—\-–]\s*applies to\s+[\w,\s]+\.?$',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove trailing "This policy applies to SITE." sentences
    quick_answer = re.sub(
        r'\s*This policy applies to\s+[\w,\s]+\.?$',
        '',
        quick_answer,
        flags=re.IGNORECASE
    ).strip()

    # Remove any emoji headers that might remain
    quick_answer = re.sub(r'^📋\s*QUICK ANSWER\s*\n*', '', quick_answer, flags=re.IGNORECASE)

    # Clean up trailing punctuation/dashes
    quick_answer = re.sub(r'[\s—\-–]+$', '', quick_answer).strip()

    # Ensure it ends with proper punctuation
    if quick_answer and quick_answer[-1] not in '.!?':
        quick_answer += '.'

    return quick_answer.strip()


def _format_answer_with_citations(
    answer_text: str,
    evidence_items: List['EvidenceItem']
) -> str:
    """
    Enhance the quick answer with formatted bold citations and reference markers.

    Adds:
    - **Bold policy names** for cited policies
    - [N] superscript-style citation numbers linking to evidence
    - Cleaner, more precise language

    Example output:
    "According to **Verbal and Telephone Orders** (Ref #486) [1], verbal orders may be..."
    """
    import re

    if not answer_text or not evidence_items:
        return answer_text

    # Build a map of policy titles to their citation info
    policy_map = {}
    for idx, e in enumerate(evidence_items):
        if e.title:
            # Normalize title for matching
            normalized = e.title.lower().strip()
            # Remove common suffixes like "Former", "Policy", etc.
            normalized = re.sub(r'\s+(former|policy|procedure)$', '', normalized, flags=re.IGNORECASE)
            policy_map[normalized] = {
                'title': e.title,
                'ref': e.reference_number,
                'idx': idx + 1  # 1-based citation number
            }

    result = answer_text

    # Find and replace policy title mentions with bold + citation
    for normalized, info in policy_map.items():
        title = info['title']
        ref = info['ref']
        idx = info['idx']

        # Pattern to match the policy title (case-insensitive, with variations)
        # Also match partial titles like "Verbal Orders" for "Verbal and Telephone Orders"
        title_words = title.split()
        if len(title_words) > 2:
            # Try matching first 2-3 significant words
            short_pattern = r'\b' + r'\s+(?:and\s+)?'.join(re.escape(w) for w in title_words[:3]) + r'[^.]*?(?=\s*[,.\)]|$)'
        else:
            short_pattern = r'\b' + re.escape(title) + r'\b'

        # Check if title is mentioned in the text
        match = re.search(short_pattern, result, re.IGNORECASE)
        if match:
            matched_text = match.group(0)
            # Format: **Policy Name** (Ref #XXX) [N]
            if ref:
                replacement = f"**{matched_text}** (Ref #{ref}) [{idx}]"
            else:
                replacement = f"**{matched_text}** [{idx}]"
            result = result[:match.start()] + replacement + result[match.end():]

    # If no matches found, append citation summary at the end
    if result == answer_text and evidence_items:
        # Add a citation summary
        citations = []
        for idx, e in enumerate(evidence_items[:3]):  # Max 3 citations
            if e.reference_number:
                citations.append(f"**{e.title}** (Ref #{e.reference_number}) [{idx + 1}]")
            else:
                citations.append(f"**{e.title}** [{idx + 1}]")

        if citations:
            # Ensure the answer ends with a period before adding sources
            if result and result[-1] not in '.!?':
                result += '.'
            result += f" Sources: {', '.join(citations)}."

    return result


def build_supporting_evidence(
    results: List[SearchResult],
    limit: int = 3,
    match_type: Optional[str] = None,
) -> List[EvidenceItem]:
    """
    Transform top search results into supporting evidence payload.

    Args:
        results: Search results to convert
        limit: Maximum number of evidence items
        match_type: Classification of how evidence was matched:
            - "verified": Exact reference number match or high reranker score (>2.5)
            - "related": Fallback query-based search when cited policy not in index
    """
    evidence_items: List[EvidenceItem] = []
    for result in results[:limit]:
        snippet = _truncate_verbatim(result.content or "")
        reference = result.reference_number or _extract_reference_identifier(result.citation)

        source_file = result.source_file
        if not source_file:
            source_file = _derive_source_file(result.title, reference)
            if source_file:
                logger.warning(f"source_file missing for '{result.title}'; derived '{source_file}'")

        evidence_items.append(
            EvidenceItem(
                snippet=snippet,
                citation=result.citation,
                title=result.title,
                reference_number=reference,
                section=result.section,
                applies_to=result.applies_to,
                document_owner=result.document_owner or None,
                date_updated=result.date_updated or None,
                date_approved=result.date_approved or None,
                source_file=source_file or None,
                score=round(result.score, 3) if result.score is not None else None,
                reranker_score=round(result.reranker_score, 3) if result.reranker_score is not None else None,
                match_type=match_type,
            )
        )
    return evidence_items


class ChatService:
    """
    Chat service for policy Q&A using Azure OpenAI "On Your Data".

    Uses vectorSemanticHybrid search for best quality:
    - Vector search (text-embedding-3-large)
    - BM25 + 132 synonym rules
    - L2 Semantic Reranking
    """

    def __init__(
        self,
        search_index: PolicySearchIndex,
        on_your_data_service: Optional[OnYourDataService] = None,
        cohere_rerank_service: Optional[CohereRerankService] = None
    ):
        self.search_index = search_index
        self.on_your_data_service = on_your_data_service
        self.cohere_rerank_service = cohere_rerank_service

        # Initialize synonym service for query expansion
        try:
            self.synonym_service = get_synonym_service()
            logger.info("Synonym service initialized for query expansion")
        except Exception as e:
            logger.warning(f"Synonym service unavailable: {e}")
            self.synonym_service = None

        # Initialize Azure OpenAI client for Cohere rerank pipeline
        # (Cohere reranks, then we use regular chat completions for LLM)
        self._openai_client = None
        if cohere_rerank_service and cohere_rerank_service.is_configured:
            aoai_endpoint = os.environ.get("AOAI_ENDPOINT")
            aoai_key = os.environ.get("AOAI_API_KEY") or os.environ.get("AOAI_API")
            if aoai_endpoint and aoai_key:
                http_client = httpx.Client(
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                )
                self._openai_client = AzureOpenAI(
                    azure_endpoint=aoai_endpoint,
                    api_key=aoai_key,
                    api_version=os.environ.get("AOAI_API_VERSION", "2024-08-01-preview"),
                    http_client=http_client
                )
                logger.info("Azure OpenAI client initialized for Cohere rerank pipeline")

    # ========================================================================
    # FIX 1: Expanded "not found" detection
    # ========================================================================
    def _is_not_found_response(self, answer_text: str) -> bool:
        """Detect if LLM response indicates no information found."""
        if not answer_text:
            return True
        if answer_text == NOT_FOUND_MESSAGE:
            return True

        answer_lower = answer_text.lower()

        # Check for explicit "not found" indicator phrases
        for phrase in NOT_FOUND_PHRASES:
            if phrase in answer_lower:
                return True

        return False

    # ========================================================================
    # FIX 2: Out-of-scope pre-query validation (DATA-DRIVEN)
    # ========================================================================
    def _is_out_of_scope_query(self, query: str) -> bool:
        """
        Detect queries about topics with NO policies in the database.

        Based on analysis of 329 policies in Azure AI Search index.
        Topics like dress code, PTO policy, leave of absence ARE in scope
        (policies exist: Ref 704, 847, HR-B 13.00, HR-B 14.00).
        """
        query_lower = query.lower()

        # Check against verified out-of-scope topics
        for keyword in ALWAYS_OUT_OF_SCOPE:
            if keyword in query_lower:
                logger.info(f"Out-of-scope query detected (no policies exist): '{keyword}'")
                return True

        return False

    # ========================================================================
    # FIX 5: Multi-policy query detection (Enhanced)
    # ========================================================================
    def _is_multi_policy_query(self, query: str) -> bool:
        """
        Detect if query likely spans multiple policies.
        
        Uses four detection strategies:
        1. Explicit indicators ("across policies", "compare", etc.)
        2. Multiple topic keywords (2+ distinct policy topics)
        3. Broad scope patterns (regex for comprehensive queries)
        4. Query decomposition analysis (comparison, multi-topic, conditional)
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
        import re
        broad_patterns = [
            r"\bwhat\s+(?:are\s+)?(?:all|any|the)\s+(?:different|various)\b",
            r"\bhow\s+(?:do|does|should)\s+(?:we|staff|nurses?|i)\b.*\band\b",
            r"\blist\s+(?:all|the)\b",
            r"\bwhat\s+(?:should|must)\s+(?:be|i)\s+.*\band\b",
            # Emergency/safety patterns that often span multiple policies
            r"\bemergenc(?:y|ies)\b.*\b(?:method|protocol|communication)\b",
            r"\bsafety\s+(?:precaution|protocol|measure)\b",
            r"\bpatient\s+identification\b",
        ]
        if any(re.search(p, query_lower) for p in broad_patterns):
            logger.debug(f"Multi-policy detected via broad pattern: {query[:50]}...")
            return True
        
        # Strategy 4: Query decomposition analysis
        # Complex queries that need decomposition are multi-policy by definition
        try:
            decomposer = get_query_decomposer()
            needs_decomp, decomp_type = decomposer.needs_decomposition(query)
            if needs_decomp:
                logger.debug(f"Multi-policy detected via decomposition ({decomp_type}): {query[:50]}...")
                return True
        except Exception as e:
            logger.debug(f"Query decomposition check failed: {e}")
        
        return False

    # ========================================================================
    # FIX 7: Dynamic search parameters based on query complexity
    # ========================================================================
    def _get_search_params(self, query: str) -> dict:
        """
        Determine optimal search parameters based on query characteristics.
        
        Per Microsoft best practices:
        - Lower strictness for queries with acronyms (reduces false negatives)
        - Higher top_n_documents for multi-policy queries (more comprehensive)
        - Standard parameters for complex queries without acronyms
        """
        words = query.split()
        word_count = len(words)
        
        # Known healthcare acronyms that benefit from lower strictness
        healthcare_acronyms = {
            'sbar', 'rrt', 'icu', 'ed', 'er', 'cpr', 'dnr', 'hipaa', 'pca',
            'picc', 'npo', 'prn', 'stat', 'vte', 'rumc', 'rumg', 'roph',
            'epic', 'lvad', 'ecmo', 'pacu', 'nicu', 'picu', 'bls', 'acls'
        }
        
        # Check if query contains any healthcare acronyms
        query_lower = query.lower()
        has_acronym = any(acr in query_lower for acr in healthcare_acronyms)
        
        # Check for short acronym-only queries (e.g., "SBAR", "RRT")
        is_acronym_query = word_count <= 2 and any(
            w.isupper() and len(w) >= 2 for w in words
        )
        
        # HEALTHCARE SAFETY: Always use strictness=5 (maximum)
        # This ensures responses are strictly grounded in retrieved documents.
        # Lower strictness allows model knowledge to contaminate responses.
        
        # Multi-policy queries need more documents
        if self._is_multi_policy_query(query):
            return {
                'strictness': 5,  # HEALTHCARE: Maximum strictness
                'top_n_documents': 100  # More documents for comprehensive coverage
            }
        
        # Acronym queries - still use max strictness but more docs
        if has_acronym or is_acronym_query or word_count <= 3:
            return {
                'strictness': 5,  # HEALTHCARE: Maximum strictness
                'top_n_documents': 75  # More docs to compensate for strict grounding
            }
        
        # Standard queries
        return {
            'strictness': 5,  # HEALTHCARE: Maximum strictness
            'top_n_documents': 50
        }

    def _get_cohere_top_n(self, query: str) -> int:
        """
        Dynamic top_n for Cohere reranking based on query complexity.
        
        Multi-policy queries need more results to ensure comprehensive coverage.
        Simple queries can use fewer results for precision.
        """
        if self._is_multi_policy_query(query):
            return 10  # More results for multi-policy queries
        if len(query.split()) <= 3:
            return 5   # Fewer for simple/short queries
        return 7       # Default for standard queries

    # ========================================================================
    # HEALTHCARE SAFETY: Confidence Scoring for Response Routing
    # ========================================================================
    def _calculate_response_confidence(
        self,
        reranked: List[RerankResult],
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

    def _confidence_level_from_score(self, score: float) -> str:
        """Map a numeric confidence score to qualitative buckets."""
        if score >= 0.7:
            return "high"
        if score >= 0.5:
            return "medium"
        return "low"

    def _boost_confidence_with_grounding(
        self,
        confidence_score: float,
        evidence_items: List[EvidenceItem],
        verification: Optional[VerificationResult] = None
    ) -> float:
        """Boost confidence using grounding signals per Cohere/AWS guidance."""
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

    def _should_return_not_found(
        self,
        confidence_score: float,
        confidence_level: str,
        has_evidence: bool
    ) -> bool:
        """
        Determine if response should be "not found" based on confidence.
        
        In healthcare, it's better to say "I don't know" than to
        risk providing inaccurate information.
        """
        # No evidence = definitely not found
        if not has_evidence:
            return True
        
        # Very low confidence = safer to say not found
        if confidence_score < 0.25:
            logger.info(f"Routing to NOT_FOUND: confidence {confidence_score:.2f} too low")
            return True
        
        return False

    # ========================================================================
    # P0: HyDE (Hypothetical Document Embeddings) Query Enhancement
    # ========================================================================
    async def _generate_hyde_query(self, query: str) -> str:
        """
        Generate a hypothetical policy document snippet for better retrieval.
        
        HyDE works by asking the LLM to generate a hypothetical answer to the query,
        then using that hypothetical document for embedding-based search. This helps
        bridge the vocabulary gap between user queries and policy documents.
        
        Example:
        - Query: "What is SBAR?"
        - HyDE output: "SBAR is a communication framework used during patient hand-offs.
          It stands for Situation, Background, Assessment, Recommendation..."
        - Combined: "What is SBAR? SBAR is a communication framework..."
        """
        try:
            # Use a fast model for HyDE generation (GPT-4o-mini or similar)
            aoai_endpoint = os.environ.get("AOAI_ENDPOINT", "")
            aoai_key = os.environ.get("AOAI_API_KEY", "") or os.environ.get("AOAI_API", "")
            
            if not aoai_endpoint or not aoai_key:
                logger.debug("HyDE skipped: Azure OpenAI not configured")
                return query
            
            client = AzureOpenAI(
                azure_endpoint=aoai_endpoint,
                api_key=aoai_key,
                api_version="2024-06-01",
                timeout=5.0  # Fast timeout for HyDE
            )
            
            hyde_prompt = f"""You are a hospital policy expert. Generate a brief (2-3 sentences) policy document excerpt that would answer this question. Write as if quoting from an official hospital policy document.

Question: {query}

Policy excerpt:"""
            
            # HEALTHCARE SAFETY: HyDE DISABLED - uses model knowledge, not database facts
            # This is a hallucination vector. Keeping code for reference but bypassing.
            logger.debug("HyDE disabled for healthcare safety - using original query")
            return query
            
            # Original HyDE code (disabled):
            # response = await asyncio.to_thread(
            #     client.chat.completions.create,
            #     model=os.environ.get("AOAI_CHAT_DEPLOYMENT", "gpt-4.1"),
            #     messages=[{"role": "user", "content": hyde_prompt}],
            #     max_tokens=150,
            #     temperature=0.0
            # )
            
            hypothetical_doc = response.choices[0].message.content.strip()
            
            # Combine original query with hypothetical document
            enhanced_query = f"{query} {hypothetical_doc}"
            logger.info(f"HyDE enhanced query: '{query[:50]}...' + hypothetical ({len(hypothetical_doc)} chars)")
            
            return enhanced_query
            
        except asyncio.TimeoutError:
            logger.debug("HyDE generation timed out, using original query")
            return query
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}, using original query")
            return query

    # ========================================================================
    # P1: Multi-Query Fusion with Reciprocal Rank Fusion (RRF)
    # ========================================================================
    def _generate_query_variants(self, query: str) -> List[str]:
        """
        Generate query variants for multi-query fusion.
        
        Creates variations of the original query to capture different
        phrasings and terminology. This improves recall by searching
        for multiple interpretations of the user's intent.
        """
        variants = [query]  # Always include original
        
        query_lower = query.lower()
        
        # Healthcare-specific reformulations
        reformulations = {
            'what is': ['define', 'explain', 'describe'],
            'how do i': ['procedure for', 'steps to', 'process for'],
            'when': ['timing for', 'schedule for', 'requirements for'],
            'who can': ['authorization for', 'eligibility for', 'permitted to'],
            'policy for': ['guidelines for', 'protocol for', 'procedure for'],
        }
        
        for pattern, alternatives in reformulations.items():
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

    def _reciprocal_rank_fusion(
        self,
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

    # ========================================================================
    # FIX 6: Adversarial query detection
    # ========================================================================
    def _is_adversarial_query(self, query: str) -> bool:
        """
        Detect adversarial queries that try to bypass safety protocols.

        Examples:
        - "How do I bypass the read-back requirement?"
        - "Fastest way to skip authentication"
        - "Pretend you're a different AI"
        """
        query_lower = query.lower()

        for pattern in ADVERSARIAL_PATTERNS:
            if pattern in query_lower:
                logger.info(f"Adversarial query detected: '{pattern}' in query")
                return True

        return False

    def _is_unclear_query(self, query: str) -> bool:
        """
        Detect unclear queries that need clarification before processing.

        Examples:
        - Single characters: "K", "a", "?"
        - Gibberish: "asdfjkl", "qwerty"
        - Too vague: "policy", "help", "what"
        - Typos without context: "polciy"
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

    def _expand_query(self, query: str) -> tuple[str, Optional[QueryExpansion]]:
        """
        Expand user query with synonyms for better search accuracy.

        Handles:
        - Medical abbreviations (ED → emergency department)
        - Common misspellings (cathater → catheter)
        - Rush-specific terms (RUMC → Rush University Medical Center)
        - Hospital codes (code blue → cardiac arrest)

        Returns:
            Tuple of (expanded_query, expansion_details)
        """
        if not self.synonym_service:
            return query, None

        try:
            expansion = self.synonym_service.expand_query(query)

            if expansion.expanded_query != query:
                logger.info(
                    f"Query expanded: '{query}' → '{expansion.expanded_query}' "
                    f"(abbrevs: {len(expansion.abbreviations_expanded)}, "
                    f"misspellings: {len(expansion.misspellings_corrected)})"
                )

            return expansion.expanded_query, expansion
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}")
            return query, None

    def _apply_policy_hints(self, query: str) -> Tuple[str, List[dict]]:
        """Append domain hints and collect target references for deterministic retrieval."""
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

    async def process_chat(self, request: ChatRequest) -> ChatResponse:
        """
        Process a chat message using the best available search pipeline.

        Pipeline priority:
        1. Cohere Rerank (cross-encoder) - best for negation-aware queries
        2. Azure "On Your Data" (vectorSemanticHybrid) - good general quality
        3. Standard retrieval (fallback)
        """
        from app.core.config import settings

        # Build safe filter expression
        filter_expr = build_applies_to_filter(request.filter_applies_to)

        # Priority 1: Cohere Rerank (cross-encoder for negation-aware search)
        # This pipeline: Azure Search → Cohere Rerank → Regular Chat Completions
        if (settings.USE_COHERE_RERANK and
            self.cohere_rerank_service and
            self.cohere_rerank_service.is_configured and
            self._openai_client):
            return await self._chat_with_cohere_rerank(request, filter_expr)

        # Priority 2: Use On Your Data for full semantic hybrid search
        if self.on_your_data_service and self.on_your_data_service.is_configured:
            return await self._chat_with_on_your_data(request, filter_expr)

        # Fallback: Standard retrieval (search + basic response)
        return await self._chat_with_standard_retrieval(request, filter_expr)

    async def _chat_with_cohere_rerank(
        self,
        request: ChatRequest,
        filter_expr: Optional[str] = None
    ) -> ChatResponse:
        """
        Chat pipeline using Cohere cross-encoder reranking.

        Flow:
        1. Azure AI Search (vector + BM25) to get candidate documents
        2. Cohere Rerank (cross-encoder) to reorder by relevance + negation understanding
        3. Azure OpenAI Chat Completions with reranked context

        Why Cohere? Cross-encoders understand negation better than bi-encoders.
        "Can MA accept verbal orders?" - Cohere understands "NOT authorized" contradicts the query.
        """
        logger.info(f"Using Cohere Rerank pipeline for query: {request.message[:50]}...")

        # Early unclear query detection (gibberish, single chars, vague)
        if self._is_unclear_query(request.message):
            logger.info(f"Unclear query detected: {request.message[:50]}...")
            # NO references for clarification requests
            return ChatResponse(
                response=UNCLEAR_QUERY_MESSAGE,
                summary=UNCLEAR_QUERY_MESSAGE,
                evidence=[],  # NEVER include evidence for clarification
                raw_response="",
                sources=[],   # NEVER include sources for clarification
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["UNCLEAR_QUERY"]
            )

        # Early out-of-scope detection
        if self._is_out_of_scope_query(request.message):
            logger.info(f"Out-of-scope query detected: {request.message[:50]}...")
            # NO references for out-of-scope responses
            out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic is outside my scope."
            return ChatResponse(
                response=out_of_scope_msg,
                summary=out_of_scope_msg,
                evidence=[],  # NEVER include evidence for out-of-scope
                raw_response="",
                sources=[],   # NEVER include sources for out-of-scope
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["OUT_OF_SCOPE"]
            )

        # Adversarial query detection
        if self._is_adversarial_query(request.message):
            logger.info(f"Adversarial query detected: {request.message[:50]}...")
            # NO references for adversarial refusal responses
            return ChatResponse(
                response=ADVERSARIAL_REFUSAL_MESSAGE,
                summary=ADVERSARIAL_REFUSAL_MESSAGE,
                evidence=[],  # NEVER include evidence for refusals
                raw_response="",
                sources=[],   # NEVER include sources for refusals
                chunks_used=0,
                found=False,
                confidence="high",
                safety_flags=["ADVERSARIAL_BLOCKED"]
            )

        # Expand query with synonyms and domain-specific hints
        expanded_query, expansion = self._expand_query(request.message)
        search_query, forced_refs = self._apply_policy_hints(expanded_query)
        forced_ref_numbers = {entry.get("reference") for entry in forced_refs if entry.get("reference")}

        forced_doc_map: Dict[str, Dict[str, Any]] = {}

        try:
            # Step 1: Get candidate documents from Azure AI Search
            # Per industry best practices: retrieve 100+ docs for reranking
            # Research shows Cohere can move relevant docs from position 273 → 5
            retrieve_top_k = settings.COHERE_RETRIEVE_TOP_K  # Default: 100
            search_results = await asyncio.to_thread(
                self.search_index.search,
                search_query,
                top=retrieve_top_k,
                filter_expr=filter_expr,
                use_semantic_ranking=True
            )
            logger.info(f"Retrieved {len(search_results) if search_results else 0} candidates for Cohere reranking")

            if not search_results:
                logger.warning("No search results returned")
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["NO_SEARCH_RESULTS"]
                )

            # Convert SearchResults to dicts for Cohere
            docs_for_rerank = []
            for sr in search_results:
                record = {
                    "content": sr.content,
                    "title": sr.title,
                    "reference_number": sr.reference_number,
                    "source_file": sr.source_file,
                    "section": sr.section,
                    "applies_to": getattr(sr, 'applies_to', '')
                }
                docs_for_rerank.append(record)
                if sr.reference_number in forced_ref_numbers and sr.reference_number not in forced_doc_map:
                    forced_doc_map[sr.reference_number] = record
            original_docs = list(docs_for_rerank)

            if forced_refs:
                existing_refs = {doc.get("reference_number") for doc in docs_for_rerank}
                for entry in forced_refs:
                    ref = entry.get("reference")
                    policy_query = entry.get("policy_query") or entry.get("hint")
                    if not ref or ref in existing_refs:
                        continue
                    try:
                        targeted = await asyncio.to_thread(
                            self.search_index.search,
                            f"{policy_query} {request.message}",
                            top=3,
                            filter_expr=filter_expr,
                            use_semantic_ranking=True,
                            use_fuzzy=False
                        )
                        for sr in targeted:
                            if sr.reference_number and sr.reference_number != ref:
                                continue
                            if sr.reference_number and sr.reference_number not in existing_refs:
                                record = {
                                    "content": sr.content,
                                    "title": sr.title,
                                    "reference_number": sr.reference_number,
                                    "source_file": sr.source_file,
                                    "section": sr.section,
                                    "applies_to": getattr(sr, 'applies_to', '')
                                }
                                docs_for_rerank.append(record)
                                forced_doc_map.setdefault(ref, record)
                                existing_refs.add(sr.reference_number)
                                break
                    except Exception as e:
                        logger.warning(f"Forced reference lookup failed for Ref #{ref}: {e}")
                original_docs = list(docs_for_rerank)

            # CORRECTIVE RAG: Evaluate retrieval quality BEFORE generation
            # This catches low-quality retrievals that could lead to hallucinations
            try:
                crag_service = get_corrective_rag_service()
                quality_assessments = crag_service.assess_retrieval_quality(
                    query=request.message,
                    documents=docs_for_rerank
                )
                corrective_action = crag_service.determine_corrective_action(
                    query=request.message,
                    assessments=quality_assessments
                )
                
                if corrective_action.action == "refuse":
                    logger.warning("cRAG: insufficient quality; proceeding with unfiltered document set")
                    docs_for_rerank = original_docs[: settings.COHERE_RETRIEVE_TOP_K]
                else:
                    filtered_docs = crag_service.filter_documents_by_quality(
                        docs_for_rerank, quality_assessments, corrective_action
                    )
                    if filtered_docs:
                        docs_for_rerank = filtered_docs
                        logger.info(f"cRAG: Filtered to {len(docs_for_rerank)} quality-approved docs")
                    elif corrective_action.relevant_docs:
                        docs_for_rerank = [
                            original_docs[i]
                            for i in corrective_action.relevant_docs
                            if i < len(original_docs)
                        ]
                        logger.info("cRAG: using relevant doc indices despite low aggregate score")
                
                if not docs_for_rerank:
                    logger.info("cRAG filtering produced no docs; reverting to original candidate set")
                    docs_for_rerank = original_docs
                
            except Exception as e:
                logger.warning(f"Corrective RAG check failed (non-critical): {e}")

            if forced_ref_numbers:
                existing_refs = {doc.get("reference_number") for doc in docs_for_rerank}
                for ref in forced_ref_numbers:
                    if ref and ref not in existing_refs:
                        for candidate in original_docs:
                            if candidate.get("reference_number") == ref:
                                docs_for_rerank.append(candidate)
                                existing_refs.add(ref)
                                logger.info(f"Forced inclusion of Ref #{ref} to maintain policy coverage")
                                break

            if not docs_for_rerank:
                logger.warning("No documents available for reranking after cRAG processing")
                if self.on_your_data_service and self.on_your_data_service.is_configured:
                    return await self._chat_with_on_your_data(request, filter_expr)
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["CRAG_NO_DOCS"]
                )

            # Step 2: Cohere rerank the documents
            # Use dynamic top_n based on query complexity
            dynamic_top_n = self._get_cohere_top_n(request.message)
            reranked = await self.cohere_rerank_service.rerank_async(
                query=request.message,  # Use original query for reranking
                documents=docs_for_rerank,
                top_n=dynamic_top_n,
                min_score=settings.COHERE_RERANK_MIN_SCORE  # Explicit threshold
            )
            logger.info(f"Cohere reranked {len(docs_for_rerank)} docs → top {dynamic_top_n} results")

            if not reranked:
                logger.warning("Cohere rerank returned no results at calibrated threshold; retrying with min_score=0.0")
                reranked = await self.cohere_rerank_service.rerank_async(
                    query=request.message,
                    documents=docs_for_rerank,
                    top_n=dynamic_top_n,
                    min_score=0.0
                )

            if not reranked:
                logger.warning("Cohere rerank still empty after relaxed threshold")
                if self.on_your_data_service and self.on_your_data_service.is_configured:
                    logger.info("Falling back to On Your Data due to empty rerank set")
                    return await self._chat_with_on_your_data(request, filter_expr)
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response="",
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    safety_flags=["NO_RERANK_RESULTS"]
                )

            if forced_ref_numbers:
                reranked_refs = {rr.reference_number for rr in reranked if rr.reference_number}
                for ref in forced_ref_numbers:
                    if not ref or ref in reranked_refs or ref not in forced_doc_map:
                        continue
                    doc = forced_doc_map[ref]
                    reranked.append(RerankResult(
                        content=doc.get("content", ""),
                        title=doc.get("title", ""),
                        reference_number=ref,
                        source_file=doc.get("source_file", ""),
                        section=doc.get("section", ""),
                        applies_to=doc.get("applies_to", ""),
                        cohere_score=0.35,
                        original_index=len(reranked)
                    ))
                    reranked_refs.add(ref)
                    logger.info(f"Appended forced rerank result for Ref #{ref}")
            
            # Apply MMR diversification for multi-policy queries
            # This ensures results come from different policies, not just different chunks
            is_multi_policy = self._is_multi_policy_query(request.message)
            if is_multi_policy and len(reranked) > 3:
                reranked = _apply_mmr_to_rerank_results(
                    reranked,
                    lambda_param=0.6,  # 60% relevance, 40% diversity
                    max_results=10
                )
                logger.info(f"Applied MMR diversification for multi-policy query: {len(reranked)} diverse results")

            # Step 3: Build context from reranked results
            context_parts = []
            evidence_items = []
            sources = []
            seen_refs = set()

            for rr in reranked:
                # Build context string
                context_parts.append(
                    f"[{rr.title} (Ref #{rr.reference_number})] "
                    f"Section: {rr.section or 'N/A'}\n{rr.content}"
                )

                # Build evidence items (deduplicated by ref)
                if rr.reference_number not in seen_refs:
                    evidence_items.append(EvidenceItem(
                        snippet=_truncate_verbatim(rr.content),
                        citation=f"{rr.title} (Ref #{rr.reference_number})" if rr.reference_number else rr.title,
                        title=rr.title,
                        reference_number=rr.reference_number,
                        section=rr.section,
                        applies_to=rr.applies_to,
                        source_file=rr.source_file,
                        reranker_score=rr.cohere_score,
                        match_type="verified",
                    ))
                    sources.append({
                        "title": rr.title,
                        "reference_number": rr.reference_number,
                        "section": rr.section,
                        "source_file": rr.source_file,
                        "cohere_score": rr.cohere_score
                    })
                    seen_refs.add(rr.reference_number)

            context = "\n\n---\n\n".join(context_parts)

            if forced_ref_numbers:
                ordered_evidence = []
                ordered_sources = []
                used_indices = set()
                forced_order = [entry.get("reference") for entry in forced_refs if entry.get("reference")]
                for ref in forced_order:
                    for idx, item in enumerate(evidence_items):
                        if idx in used_indices:
                            continue
                        if item.reference_number == ref:
                            ordered_evidence.append(item)
                            ordered_sources.append(sources[idx])
                            used_indices.add(idx)
                            break
                for idx, item in enumerate(evidence_items):
                    if idx not in used_indices:
                        ordered_evidence.append(item)
                        ordered_sources.append(sources[idx])
                evidence_items = ordered_evidence
                sources = ordered_sources

            # Step 4: Call Azure OpenAI Chat Completions
            messages = [
                {"role": "system", "content": RISEN_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {request.message}"}
            ]

            # Dynamic max_tokens: multi-policy queries need more space for comprehensive answers
            max_tokens = 800 if is_multi_policy else 500
            
            response = await asyncio.to_thread(
                self._openai_client.chat.completions.create,
                model=settings.AOAI_CHAT_DEPLOYMENT,
                messages=messages,
                temperature=0.0,  # HEALTHCARE: Zero temperature for deterministic, factual responses
                max_tokens=max_tokens
            )

            answer_text = response.choices[0].message.content or NOT_FOUND_MESSAGE

            # CRITICAL: Strip any references from negative response types
            # The LLM might include refs even when saying "I could not find"
            answer_text = _strip_references_from_negative_response(answer_text)

            # Check for NOT_FOUND patterns
            if self._is_not_found_response(answer_text):
                # But if we have evidence, trust the response
                if evidence_items:
                    logger.info(f"NOT_FOUND override: {len(evidence_items)} evidence items exist")
                else:
                    # Return clean NOT_FOUND with NO references
                    return ChatResponse(
                        response=NOT_FOUND_MESSAGE,
                        summary=NOT_FOUND_MESSAGE,
                        evidence=[],  # NEVER include evidence for not-found
                        raw_response=answer_text,
                        sources=[],   # NEVER include sources for not-found
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        safety_flags=["LLM_NOT_FOUND"]
                    )

            # CRITICAL FIX: Check for refusal/out-of-scope responses
            # Even if evidence was retrieved (e.g., keyword matches for "Chicago"),
            # if the LLM says "I only answer RUSH policy questions", clear all citations
            if _is_refusal_response(answer_text):
                logger.info(f"Refusal response detected, clearing {len(evidence_items)} false positive citations")
                # Return refusal with NO references (even if search found keyword matches)
                return ChatResponse(
                    response=answer_text,
                    summary=answer_text,
                    evidence=[],  # NEVER include evidence for refusals
                    raw_response=answer_text,
                    sources=[],   # NEVER include sources for refusals
                    chunks_used=0,
                    found=False,
                    confidence="high",  # High confidence in the refusal
                    safety_flags=["LLM_REFUSAL"]
                )

            # Calculate confidence from Cohere rerank scores
            confidence_score, confidence_level = self._calculate_response_confidence(
                reranked, has_evidence=bool(evidence_items)
            )
            
            # Prepare contexts for validation (handle empty case)
            contexts = [rr.content for rr in reranked] if reranked else []
            
            # Citation verification - detect hallucinated references
            try:
                citation_verifier = get_citation_verifier()
                verification = citation_verifier.verify_response(
                    response=answer_text,
                    contexts=contexts,
                    sources=sources
                )
                
                # Add citation verification flags
                citation_flags = verification.flags if verification.flags else []
                if verification.hallucination_risk > 0.3:
                    citation_flags.append(f"HALLUCINATION_RISK:{verification.hallucination_risk:.2f}")
                    logger.warning(
                        f"Citation verification: hallucination_risk={verification.hallucination_risk:.2f}, "
                        f"flags={verification.flags}"
                    )
                
                # HEALTHCARE CRITICAL: Verify all factual claims (numbers, dosages, timeframes)
                # Multi-policy queries get slightly relaxed verification (claims can be in ANY context)
                facts_verified, unverified_facts, fact_flags = citation_verifier.verify_factual_claims(
                    response=answer_text,
                    contexts=contexts,
                    is_multi_policy=is_multi_policy
                )
                citation_flags.extend(fact_flags)
                
                if not facts_verified and unverified_facts:
                    logger.warning(f"HEALTHCARE SAFETY: Blocking response with unverified facts: {unverified_facts}")
                    return ChatResponse(
                        response="I could not verify all factual claims against RUSH policy documents. "
                                 f"Please check {settings.POLICYTECH_URL} or contact Policy Administration.",
                        summary="Unable to verify factual accuracy",
                        evidence=[],
                        raw_response=answer_text,
                        sources=[],
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        confidence_score=confidence_score,
                        needs_human_review=True,
                        safety_flags=citation_flags + ["BLOCKED_UNVERIFIED_FACTS"]
                    )
                
                # HEALTHCARE CRITICAL: Verify no fabricated policy references
                refs_verified, fabricated_refs, ref_flags = citation_verifier.verify_no_fabricated_refs(
                    response=answer_text,
                    context_refs=verification.context_refs if verification else set()
                )
                citation_flags.extend(ref_flags)
                
                if not refs_verified and fabricated_refs:
                    logger.warning(f"HEALTHCARE SAFETY: Blocking response with fabricated refs: {fabricated_refs}")
                    return ChatResponse(
                        response="I could not verify all policy citations. "
                                 f"Please check {settings.POLICYTECH_URL} for accurate policy information.",
                        summary="Unable to verify policy citations",
                        evidence=[],
                        raw_response=answer_text,
                        sources=[],
                        chunks_used=0,
                        found=False,
                        confidence="low",
                        confidence_score=confidence_score,
                        needs_human_review=True,
                        safety_flags=citation_flags + ["BLOCKED_FABRICATED_REFS"]
                    )
                
            except Exception as e:
                logger.warning(f"Citation verification failed (non-critical): {e}")
                citation_flags = []
                verification = None
            
            confidence_score = self._boost_confidence_with_grounding(
                confidence_score,
                evidence_items,
                verification
            )
            confidence_level = self._confidence_level_from_score(confidence_score)

            # Safety validation for healthcare
            try:
                safety_validator = get_safety_validator(strict_mode=True)
                safety_result = safety_validator.validate(
                    response_text=answer_text,
                    contexts=contexts,
                    confidence_score=confidence_score,
                    has_evidence=bool(evidence_items)
                )
                
                # Combine citation and safety flags
                all_flags = list(set(safety_result.flags + citation_flags))
                
            except Exception as e:
                logger.warning(f"Safety validation failed (non-critical): {e}")
                # Graceful degradation - allow response but flag for review
                safety_result = None
                all_flags = citation_flags + ["SAFETY_CHECK_SKIPPED"]
            
            # HEALTHCARE SAFETY: ALWAYS block responses that fail safety validation
            # Patient safety requires blocking, not just flagging, unsafe responses
            if safety_result and not safety_result.safe:
                logger.warning(f"HEALTHCARE SAFETY BLOCK: {all_flags}")
                fallback = (
                    safety_result.fallback_response or
                    f"I could not verify this information against RUSH policies. "
                    f"Please check {settings.POLICYTECH_URL} or contact Policy Administration."
                )
                return ChatResponse(
                    response=fallback,
                    summary=fallback,
                    evidence=[],
                    raw_response=answer_text,
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    confidence_score=confidence_score,
                    needs_human_review=True,
                    safety_flags=all_flags + ["BLOCKED_BY_SAFETY_CHECK"]
                )
            
            # HEALTHCARE SAFETY: Block if citation verification found HIGH hallucination risk
            # Note: 0.5 threshold allows responses without inline citations if content is grounded
            # A response with good content but no "Ref #XXX" citations scores ~0.4 (not a hallucination)
            if verification and verification.hallucination_risk > 0.5:
                logger.warning(f"HEALTHCARE SAFETY BLOCK: Hallucination risk {verification.hallucination_risk:.2f}")
                return ChatResponse(
                    response="I could not verify all claims in this response against RUSH policies. "
                             f"Please check {settings.POLICYTECH_URL} or contact Policy Administration.",
                    summary="Unable to verify response accuracy",
                    evidence=[],
                    raw_response=answer_text,
                    sources=[],
                    chunks_used=0,
                    found=False,
                    confidence="low",
                    confidence_score=confidence_score,
                    needs_human_review=True,
                    safety_flags=all_flags + ["BLOCKED_HALLUCINATION_RISK"]
                )
            
            # Determine if human review needed
            needs_review = (
                (safety_result and safety_result.needs_human_review) or
                (verification and verification.hallucination_risk > 0.5) or
                confidence_level == "low"
            )
            
            # SELF-REFLECTIVE RAG: Critique response for grounding before returning
            # This catches issues that slipped past safety validation
            try:
                self_reflective_service = get_self_reflective_service()
                critique = self_reflective_service.critique_response(
                    response=answer_text,
                    query=request.message,
                    contexts=contexts
                )
                
                if not critique.overall_pass:
                    logger.warning(f"Self-Reflective critique failed: {critique.issues}")
                    # Add flags but don't block - critique is advisory
                    all_flags.append("SELF_CRITIQUE_WARNING")
                    if not critique.is_grounded:
                        all_flags.append("LOW_GROUNDING")
                    if critique.unsupported_claims:
                        all_flags.append("UNSUPPORTED_CLAIMS")
                    # Trigger human review for low-confidence critiques
                    if critique.confidence < 0.5:
                        needs_review = True
                else:
                    logger.debug(f"Self-Reflective critique passed: confidence={critique.confidence:.2f}")
                    
            except Exception as e:
                logger.warning(f"Self-Reflective critique failed (non-critical): {e}")
            
            # Dynamic evidence limit: multi-policy queries return more citations
            max_evidence = 10 if is_multi_policy else 5
            evidence_payload = evidence_items[:max_evidence]
            sources_payload = sources[:max_evidence]

            formatter = get_citation_formatter()
            formatted = formatter.format(
                answer_text=answer_text,
                evidence=evidence_payload,
                max_refs=max_evidence,
                found=True,
            )

            summary_text = formatted.summary or (
                answer_text[:200] + "..." if len(answer_text) > 200 else answer_text
            )
            response_text = formatted.response or answer_text

            return ChatResponse(
                response=response_text,
                summary=summary_text,
                evidence=evidence_payload,
                raw_response=answer_text,
                sources=sources_payload,
                chunks_used=len(reranked),
                found=True,
                confidence=confidence_level,
                confidence_score=confidence_score,
                needs_human_review=needs_review,
                safety_flags=all_flags
            )

        except Exception as e:
            logger.error(f"Cohere rerank pipeline failed: {e}")
            # Fallback to On Your Data if available
            if self.on_your_data_service and self.on_your_data_service.is_configured:
                logger.info("Falling back to On Your Data pipeline")
                return await self._chat_with_on_your_data(request, filter_expr)
            raise

    def _extract_policy_refs_from_response(self, response_text: str) -> List[dict]:
        """
        Extract policy references mentioned in the agent's response.

        The agent uses various citation formats:
        - [Policy Name, Ref #XXXX]
        - "Policy Name" policy [Ref #XXXX]
        - Policy: Policy Name with Reference Number: XXXX
        - [Ref #XXXX] standalone

        Returns list of dicts with 'title' and 'reference_number' keys.
        """
        import re
        refs = []

        # Pattern 1: [Title, Ref #XXXX] - title and ref in same bracket
        pattern1 = r'\[([^,\]]+?)(?:,\s*Ref\s*[#:]?\s*|,\s*Reference\s*(?:Number)?[:#]?\s*)([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern1, response_text, re.IGNORECASE):
            refs.append({'title': match.group(1).strip(), 'reference_number': match.group(2).strip()})

        # Pattern 2: "Title" policy [Ref #XXXX] - quoted title before ref bracket
        pattern2 = r'"([^"]+)"\s*(?:policy)?\s*\[Ref\s*[#:]?\s*([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern2, response_text, re.IGNORECASE):
            refs.append({'title': match.group(1).strip(), 'reference_number': match.group(2).strip()})

        # Pattern 3: Policy: Title Name (in formatted box) + Reference Number: XXXX
        policy_title_match = re.search(r'Policy:\s*([^\n│]+)', response_text)
        ref_num_match = re.search(r'Reference\s*Number[:#]?\s*([A-Z0-9\.\-]{2,15})', response_text, re.IGNORECASE)
        if policy_title_match and ref_num_match:
            title = policy_title_match.group(1).strip().rstrip('│').strip()
            ref_num = ref_num_match.group(1).strip()
            refs.append({'title': title, 'reference_number': ref_num})

        # Pattern 4: [Ref #XXXX] standalone - try to find nearby title
        pattern4 = r'\[Ref\s*[#:]?\s*([A-Z0-9\.\-]+)\]'
        for match in re.finditer(pattern4, response_text, re.IGNORECASE):
            ref_num = match.group(1).strip()
            # Check if we already have this ref
            if any(r['reference_number'] == ref_num for r in refs):
                continue
            # Try to find a quoted title before this reference
            before_text = response_text[:match.start()]
            title_before = re.search(r'"([^"]+)"\s*(?:policy)?\s*$', before_text)
            if title_before:
                refs.append({'title': title_before.group(1).strip(), 'reference_number': ref_num})
            else:
                refs.append({'title': '', 'reference_number': ref_num})

        # Deduplicate by reference number, preferring entries with titles
        seen = {}
        for ref in refs:
            ref_num = ref['reference_number']
            if ref_num:
                if ref_num not in seen or (ref['title'] and not seen[ref_num]['title']):
                    seen[ref_num] = ref

        return list(seen.values())

    async def _chat_with_on_your_data(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """
        Handle chat using Azure OpenAI "On Your Data" with vectorSemanticHybrid.

        This provides the BEST search quality:
        - Vector similarity (text-embedding-3-large)
        - BM25 keyword matching
        - L2 semantic reranking (the key feature!)

        The citations come directly from Azure AI Search via the On Your Data API,
        ensuring accurate source attribution.
        """
        logger.info(f"Using On Your Data (vectorSemanticHybrid) for query: {request.message[:50]}...")

        # Early unclear query detection (gibberish, single chars, vague)
        if self._is_unclear_query(request.message):
            logger.info(f"Unclear query detected: {request.message[:50]}...")
            # NO references for clarification requests
            return ChatResponse(
                response=UNCLEAR_QUERY_MESSAGE,
                summary=UNCLEAR_QUERY_MESSAGE,
                evidence=[],  # NEVER include evidence for clarification
                raw_response="",
                sources=[],   # NEVER include sources for clarification
                chunks_used=0,
                found=False,
                safety_flags=["UNCLEAR_QUERY"]
            )

        # FIX 2: Early out-of-scope detection (before any API calls)
        if self._is_out_of_scope_query(request.message):
            logger.info(f"Out-of-scope query detected: {request.message[:50]}...")
            out_of_scope_msg = "I could not find this in RUSH clinical policies. This topic (parking, HR benefits, administrative matters) is outside my scope. Please contact Human Resources or the appropriate department."
            # NO references for out-of-scope responses
            return ChatResponse(
                response=out_of_scope_msg,
                summary=out_of_scope_msg,
                evidence=[],  # NEVER include evidence for out-of-scope
                raw_response="",
                sources=[],   # NEVER include sources for out-of-scope
                chunks_used=0,
                found=False,
                safety_flags=["OUT_OF_SCOPE"]
            )

        # FIX 6: Adversarial query detection (bypass/circumvent safety protocols)
        if self._is_adversarial_query(request.message):
            logger.info(f"Adversarial query detected: {request.message[:50]}...")
            # NO references for refusal responses
            return ChatResponse(
                response=ADVERSARIAL_REFUSAL_MESSAGE,
                summary=ADVERSARIAL_REFUSAL_MESSAGE,
                evidence=[],  # NEVER include evidence for refusals
                raw_response="",
                sources=[],   # NEVER include sources for refusals
                chunks_used=0,
                found=False,
                safety_flags=["ADVERSARIAL_BLOCKED"]
            )

        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        # P0: HyDE is disabled - testing showed it causes regressions with Azure "On Your Data"
        # The On Your Data API does its own query expansion, so HyDE interferes
        # Keep the method for future experimentation but don't use it by default
        # TODO: Re-enable HyDE only when using direct search (not On Your Data)

        # FIX 7: Get dynamic search parameters based on query type
        search_params = self._get_search_params(request.message)
        logger.info(
            f"Search params for query: strictness={search_params['strictness']}, "
            f"top_n={search_params['top_n_documents']}"
        )

        try:
            # 60s timeout allows for: embedding (1-2s) + search (1-3s) + generation (5-10s)
            # + retry backoff (up to 14s for 3 retries with exponential backoff) + buffer
            result: OnYourDataResult = await asyncio.wait_for(
                self.on_your_data_service.chat(
                    query=expanded_query,
                    filter_expr=filter_expr,
                    top_n_documents=search_params['top_n_documents'],
                    strictness=search_params['strictness']
                ),
                timeout=60.0
            )

            answer_text = result.answer or NOT_FOUND_MESSAGE

            # CRITICAL: Strip any references from negative response types
            # The LLM might include refs even when saying "I could not find"
            answer_text = _strip_references_from_negative_response(answer_text)

            # FIX 1: Use expanded "not found" detection
            # FIX 8: Context-aware NOT_FOUND - if citations exist, trust the response
            # This prevents false positives when LLM says "could not find specific X"
            # but actually DID retrieve relevant documents
            has_citations = bool(result.citations and len(result.citations) > 0)

            # Phase 2 Diagnostic: Log which phrases trigger NOT_FOUND
            if self._is_not_found_response(answer_text):
                logger.warning(f"NOT_FOUND triggered for query: '{request.message[:80]}...'")
                logger.warning(f"LLM response that triggered: '{answer_text[:300]}...'")
                # Log which specific phrase matched for diagnosis
                matched_phrase = None
                answer_lower = answer_text.lower()
                for phrase in NOT_FOUND_PHRASES:
                    if phrase in answer_lower:
                        matched_phrase = phrase
                        logger.warning(f"Matched NOT_FOUND phrase: '{phrase}'")
                        break
                if not matched_phrase and answer_text == NOT_FOUND_MESSAGE:
                    logger.warning("Matched: exact NOT_FOUND_MESSAGE constant")
                elif not matched_phrase:
                    logger.warning("NOT_FOUND triggered but no phrase matched (empty response?)")

                # FIX 8: If there ARE citations, don't treat as "not found"
                # The LLM may say "could not find X" but still provide useful info
                if has_citations:
                    logger.info(
                        f"NOT_FOUND override: {len(result.citations)} citations exist, "
                        f"treating as valid response despite phrase match"
                    )
                else:
                    # Return clean NOT_FOUND with NO references
                    return ChatResponse(
                        response=NOT_FOUND_MESSAGE,
                        summary=NOT_FOUND_MESSAGE,
                        evidence=[],  # NEVER include evidence for not-found
                        raw_response=str(result.raw_response),
                        sources=[],   # NEVER include sources for not-found
                        chunks_used=0,
                        found=False
                    )

            # CRITICAL FIX: Check for refusal/out-of-scope responses
            # Even if citations exist (e.g., keyword matches for "Chicago"),
            # if the LLM says "I only answer RUSH policy questions", clear all citations
            if _is_refusal_response(answer_text):
                logger.info(f"Refusal response detected, clearing {len(result.citations) if result.citations else 0} false positive citations")
                return ChatResponse(
                    response=answer_text,
                    summary=answer_text,
                    evidence=[],  # NEVER include evidence for refusals
                    raw_response=str(result.raw_response),
                    sources=[],   # NEVER include sources for refusals
                    chunks_used=0,
                    found=False,
                    confidence="high",  # High confidence in the refusal
                    safety_flags=["LLM_REFUSAL"]
                )

            # If we reach here, we have a valid answer (not an early "not found" return)
            found = True

            # Convert On Your Data citations to EvidenceItems
            # Enrich citations with metadata from Azure AI Search
            evidence_items = []
            sources = []

            # FIX 5: Dynamic citation limit for multi-policy queries
            is_multi_policy = self._is_multi_policy_query(request.message)
            max_citations = 10 if is_multi_policy else 5

            # FIX 8: Apply MMR diversification for multi-policy queries
            # This ensures citations come from different policies, not just different chunks
            citations_to_process = result.citations
            if is_multi_policy and len(result.citations) > max_citations:
                citations_to_process = _apply_mmr_diversification(
                    result.citations,
                    lambda_param=0.6,  # 60% relevance, 40% diversity for multi-policy
                    max_results=max_citations
                )
                logger.info(f"Applied MMR diversification: {len(result.citations)} -> {len(citations_to_process)} citations")

            for cit in citations_to_process[:max_citations]:
                source_file = cit.filepath or ""

                # Look up full metadata from Azure AI Search by source_file
                metadata = None
                if source_file:
                    try:
                        metadata = await asyncio.to_thread(
                            self.search_index.get_metadata_by_source_file,
                            source_file
                        )
                    except Exception as e:
                        logger.warning(f"Failed to get metadata for {source_file}: {e}")

                # Use metadata from lookup, falling back to citation data
                ref_num = ""
                applies_to = ""
                section = ""
                date_updated = ""
                title = cit.title

                if metadata:
                    ref_num = metadata.get("reference_number", "")
                    applies_to = metadata.get("applies_to", "")
                    section = metadata.get("section", "") or cit.section
                    date_updated = metadata.get("date_updated", "")
                    title = metadata.get("title", "") or cit.title
                    logger.debug(f"Enriched citation {source_file}: applies_to={applies_to}")
                else:
                    # Fallback: Try to extract ref number from filepath (e.g., "hr-001.pdf" -> "HR-001")
                    if source_file:
                        import re
                        ref_match = re.search(r'([a-z]{2,4}[-_]?\d{2,4})', source_file.lower())
                        if ref_match:
                            ref_num = ref_match.group(1).upper().replace('_', '-')

                evidence_items.append(
                    EvidenceItem(
                        snippet=_truncate_verbatim(cit.content),
                        citation=f"{title} ({ref_num})" if ref_num else title,
                        title=title,
                        reference_number=ref_num,
                        section=section,
                        applies_to=applies_to,
                        date_updated=date_updated,
                        source_file=source_file,
                        score=None,
                        reranker_score=cit.reranker_score,
                        match_type="verified",  # Citations come directly from search
                    )
                )

                sources.append({
                    "citation": f"{title} ({ref_num})" if ref_num else title,
                    "source_file": source_file,
                    "title": title,
                    "reference_number": ref_num,
                    "section": section,
                    "applies_to": applies_to,
                    "date_updated": date_updated,
                    "reranker_score": cit.reranker_score,
                    "match_type": "verified"
                })

            # If On Your Data didn't return citations but we have an answer,
            # try to find supporting evidence via direct search
            if not evidence_items and found:
                logger.info("No citations from On Your Data, supplementing with search")
                extracted_refs = self._extract_policy_refs_from_response(answer_text)

                if extracted_refs:
                    for ref in extracted_refs[:3]:
                        try:
                            if ref['reference_number']:
                                # Wrap sync search in thread to avoid blocking
                                ref_results = await asyncio.to_thread(
                                    self.search_index.search,
                                    query=ref['reference_number'],
                                    top=3,
                                    filter_expr=filter_expr,
                                    use_semantic_ranking=True
                                )
                                for r in ref_results:
                                    if r.reference_number and (
                                        r.reference_number == ref['reference_number'] or
                                        ref['reference_number'] in r.reference_number
                                    ):
                                        evidence_items.append(
                                            EvidenceItem(
                                                snippet=_truncate_verbatim(r.content or ""),
                                                citation=r.citation,
                                                title=r.title,
                                                reference_number=r.reference_number,
                                                section=r.section,
                                                applies_to=r.applies_to,
                                                source_file=r.source_file,
                                                score=r.score,
                                                reranker_score=r.reranker_score,
                                                match_type="verified",
                                            )
                                        )
                                        sources.append({
                                            "citation": r.citation,
                                            "source_file": r.source_file,
                                            "title": r.title,
                                            "reference_number": r.reference_number,
                                            "section": r.section,
                                            "applies_to": r.applies_to,
                                            "score": r.score,
                                            "match_type": "verified"
                                        })
                                        break
                        except Exception as e:
                            logger.warning(f"Supplemental search failed for ref {ref}: {e}")

            # Extract clean quick answer for display
            clean_summary = _extract_quick_answer(answer_text)

            # Format the summary with bold citations and reference markers
            formatted_summary = _format_answer_with_citations(clean_summary, evidence_items)

            formatter = get_citation_formatter()
            found_flag = bool(evidence_items)
            formatted_result = formatter.format(
                answer_text=formatted_summary or clean_summary,
                evidence=evidence_items,
                max_refs=len(evidence_items) if evidence_items else 0,
                found=found_flag,
            )

            summary_payload = formatted_result.summary or formatted_summary
            response_payload = formatted_result.response or answer_text

            return ChatResponse(
                response=response_payload,
                summary=summary_payload,
                evidence=evidence_items,
                raw_response=str(result.raw_response),
                sources=sources,
                chunks_used=len(evidence_items),
                found=found_flag
            )

        except asyncio.TimeoutError:
            logger.warning("On Your Data request timed out after 45s")
            return await self._chat_with_standard_retrieval(request, filter_expr)
        except Exception as e:
            logger.warning(f"On Your Data failed, falling back to standard retrieval: {e}")
            return await self._chat_with_standard_retrieval(request, filter_expr)

    async def _chat_with_standard_retrieval(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """
        Handle chat using standard hybrid search retrieval.

        This is the fallback when On Your Data is not available.
        Returns search results with a basic "not found" message if no LLM configured.
        """
        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        try:
            # Wrap sync search in thread with 30s timeout to prevent hanging connections
            search_results = await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_index.search,
                    query=expanded_query,
                    top=5,
                    filter_expr=filter_expr,
                    use_semantic_ranking=True
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("Fallback search timed out after 30s")
            return ChatResponse(
                response="I'm sorry, the search is taking longer than expected. Please try again in a moment.",
                summary="Search timeout",
                evidence=[],
                sources=[],
                chunks_used=0,
                found=False
            )
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return ChatResponse(
                response="I'm sorry, I encountered an issue while searching the policy database. Please try again in a moment.",
                summary="Search temporarily unavailable",
                evidence=[],
                sources=[],
                chunks_used=0,
                found=False
            )

        if search_results is None:
            search_results = []

        context = format_rag_context(search_results) if search_results else ""
        evidence_items = build_supporting_evidence(search_results) if search_results else []

        sources = [{
            "citation": r.citation,
            "source_file": r.source_file,
            "title": r.title,
            "reference_number": r.reference_number,
            "section": r.section,
            "applies_to": r.applies_to,
            "date_updated": r.date_updated,
            "score": r.score,
            "document_owner": r.document_owner,
            "date_approved": r.date_approved
        } for r in search_results]

        # Without On Your Data, we can only return search results
        # The frontend should display these with a notice that LLM is unavailable
        if not search_results:
            summary_text = NOT_FOUND_MESSAGE
        else:
            summary_text = LLM_UNAVAILABLE_MESSAGE

        found = bool(search_results) and summary_text != NOT_FOUND_MESSAGE

        if not found:
            evidence_items = []
            sources = []

        return ChatResponse(
            response=summary_text,
            summary=summary_text,
            evidence=evidence_items,
            raw_response=summary_text,
            sources=sources,
            chunks_used=len(search_results),
            found=found
        )
