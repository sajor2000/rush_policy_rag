"""
Response formatting utilities for the RUSH Policy RAG system.

This module contains functions for:
- Citation extraction and formatting
- Quick answer extraction from RISEN responses
- Source file derivation
- Supporting evidence construction

Extracted from chat_service.py as part of tech debt refactoring.
"""

import logging
import re
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from azure_policy_index import SearchResult
    from app.models.schemas import EvidenceItem

from app.services.query_processor import truncate_verbatim, normalize_policy_title

logger = logging.getLogger(__name__)


def extract_reference_identifier(citation: str) -> str:
    """
    Best-effort extraction of reference number from citation text.

    Parses patterns like "Policy Name (Ref: 486)" to extract "486".

    Args:
        citation: Citation text containing reference number in parentheses

    Returns:
        Extracted reference number or empty string if not found
    """
    if not citation or "(" not in citation or ")" not in citation:
        return ""

    try:
        inner = citation.split("(", 1)[1].split(")", 1)[0]
        return inner.replace("Ref:", "").strip()
    except (IndexError, ValueError):
        return ""


def derive_source_file(title: str, reference_number: str) -> str:
    """
    Derive a plausible source_file from title/reference when missing.

    Used as fallback when search results don't include source_file metadata.

    Args:
        title: Policy title
        reference_number: Policy reference number (e.g., "486")

    Returns:
        Derived filename like "486.pdf" or "verbal-and-telephone-orders.pdf"
    """
    if reference_number:
        return f"{reference_number.lower().replace(' ', '-')}.pdf"
    if title:
        slug = title.lower().replace(" ", "-").replace("/", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        return f"{slug[:80]}.pdf"
    return ""


def extract_quick_answer(response_text: str) -> str:
    """
    Extract just the quick answer portion from a RISEN-formatted response.

    Strips out:
    - QUICK ANSWER header
    - POLICY REFERENCE section with ASCII box
    - NOTICE footer
    - Citation lines at the end of quick answer

    Returns clean prose suitable for display in the Quick Answer UI box.

    Args:
        response_text: Full LLM response text

    Returns:
        Cleaned quick answer text suitable for display
    """
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


def format_answer_with_citations(
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

    Args:
        answer_text: The quick answer text to enhance
        evidence_items: List of evidence items with policy titles/references

    Returns:
        Enhanced answer text with inline citations
    """
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
    results: List['SearchResult'],
    limit: int = 3,
    match_type: Optional[str] = None,
) -> List['EvidenceItem']:
    """
    Transform top search results into supporting evidence payload.

    Creates EvidenceItem objects from SearchResult objects with proper
    formatting, truncation, and metadata derivation.

    Args:
        results: Search results to convert
        limit: Maximum number of evidence items
        match_type: Classification of how evidence was matched:
            - "verified": Exact reference number match or high reranker score (>2.5)
            - "related": Fallback query-based search when cited policy not in index

    Returns:
        List of EvidenceItem objects ready for API response
    """
    # Import here to avoid circular imports
    from azure_policy_index import SearchResult
    from app.models.schemas import EvidenceItem

    evidence_items: List[EvidenceItem] = []
    for result in results[:limit]:
        snippet = truncate_verbatim(result.content or "")
        reference = result.reference_number or extract_reference_identifier(result.citation)
        title = normalize_policy_title(result.title)

        source_file = result.source_file
        if not source_file:
            source_file = derive_source_file(result.title, reference)
            if source_file:
                logger.warning(f"source_file missing for '{result.title}'; derived '{source_file}'")

        evidence_items.append(
            EvidenceItem(
                snippet=snippet,
                citation=result.citation,
                title=title,
                reference_number=reference,
                section=result.section,
                applies_to=result.applies_to,
                document_owner=result.document_owner or None,
                date_updated=result.date_updated or None,
                date_approved=result.date_approved or None,
                source_file=source_file or None,
                page_number=result.page_number,
                score=round(result.score, 3) if result.score is not None else None,
                reranker_score=round(result.reranker_score, 3) if result.reranker_score is not None else None,
                match_type=match_type,
            )
        )
    return evidence_items
