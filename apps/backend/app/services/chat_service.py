import logging
import asyncio
from typing import Optional, List
from app.models.schemas import ChatRequest, ChatResponse, EvidenceItem
from app.core.prompts import RISEN_PROMPT, NOT_FOUND_MESSAGE, LLM_UNAVAILABLE_MESSAGE
from app.core.security import build_applies_to_filter
from azure_policy_index import PolicySearchIndex, format_rag_context, SearchResult
from app.services.on_your_data_service import OnYourDataService, OnYourDataResult
from app.services.synonym_service import get_synonym_service, QueryExpansion

logger = logging.getLogger(__name__)

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
        on_your_data_service: Optional[OnYourDataService] = None
    ):
        self.search_index = search_index
        self.on_your_data_service = on_your_data_service

        # Initialize synonym service for query expansion
        try:
            self.synonym_service = get_synonym_service()
            logger.info("Synonym service initialized for query expansion")
        except Exception as e:
            logger.warning(f"Synonym service unavailable: {e}")
            self.synonym_service = None

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

    async def process_chat(self, request: ChatRequest) -> ChatResponse:
        """
        Process a chat message using Azure OpenAI "On Your Data" (vectorSemanticHybrid).

        Uses vectorSemanticHybrid search for best quality:
        - Vector search (text-embedding-3-large)
        - BM25 + 132 synonym rules
        - L2 Semantic Reranking
        """
        # Build safe filter expression
        filter_expr = build_applies_to_filter(request.filter_applies_to)

        # Primary: Use On Your Data for full semantic hybrid search
        if self.on_your_data_service and self.on_your_data_service.is_configured:
            return await self._chat_with_on_your_data(request, filter_expr)

        # Fallback: Standard retrieval (search + basic response)
        return await self._chat_with_standard_retrieval(request, filter_expr)

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

        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        try:
            # Add 15s timeout to prevent hanging requests
            result: OnYourDataResult = await asyncio.wait_for(
                self.on_your_data_service.chat(
                    query=expanded_query,
                    filter_expr=filter_expr,
                    top_n_documents=50,  # Get many for reranker to choose from
                    strictness=3
                ),
                timeout=15.0
            )

            answer_text = result.answer or NOT_FOUND_MESSAGE

            # Check if we got a meaningful answer
            found = (
                answer_text and
                answer_text != NOT_FOUND_MESSAGE and
                "I don't have" not in answer_text.lower() and
                "no information" not in answer_text.lower()
            )

            if not found:
                return ChatResponse(
                    response=NOT_FOUND_MESSAGE,
                    summary=NOT_FOUND_MESSAGE,
                    evidence=[],
                    raw_response=str(result.raw_response),
                    sources=[],
                    chunks_used=0,
                    found=False
                )

            # Convert On Your Data citations to EvidenceItems
            evidence_items = []
            sources = []

            for cit in result.citations[:5]:  # Limit to top 5 citations
                # Extract reference number from filepath or content
                ref_num = ""
                source_file = cit.filepath or ""

                # Try to extract ref number from filepath (e.g., "hr-001.pdf" -> "HR-001")
                if source_file:
                    import re
                    ref_match = re.search(r'([a-z]{2,4}[-_]?\d{2,4})', source_file.lower())
                    if ref_match:
                        ref_num = ref_match.group(1).upper().replace('_', '-')

                evidence_items.append(
                    EvidenceItem(
                        snippet=_truncate_verbatim(cit.content),
                        citation=f"{cit.title} ({ref_num})" if ref_num else cit.title,
                        title=cit.title,
                        reference_number=ref_num,
                        section=cit.section,
                        applies_to=cit.applies_to,
                        source_file=source_file,
                        score=None,
                        reranker_score=cit.reranker_score,
                        match_type="verified",  # Citations come directly from search
                    )
                )

                sources.append({
                    "citation": f"{cit.title} ({ref_num})" if ref_num else cit.title,
                    "source_file": source_file,
                    "title": cit.title,
                    "reference_number": ref_num,
                    "section": cit.section,
                    "applies_to": cit.applies_to,
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

            return ChatResponse(
                response=answer_text,
                summary=clean_summary,
                evidence=evidence_items,
                raw_response=str(result.raw_response),
                sources=sources,
                chunks_used=len(evidence_items),
                found=bool(evidence_items)
            )

        except asyncio.TimeoutError:
            logger.warning("On Your Data request timed out after 15s")
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
            # Wrap sync search in thread to avoid blocking
            search_results = await asyncio.to_thread(
                self.search_index.search,
                query=expanded_query,
                top=5,
                filter_expr=filter_expr,
                use_semantic_ranking=True
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
