import logging
from typing import Optional, List
from app.models.schemas import ChatRequest, ChatResponse, EvidenceItem
from app.core.prompts import RISEN_PROMPT, NOT_FOUND_MESSAGE, LLM_UNAVAILABLE_MESSAGE
from app.core.security import build_applies_to_filter
from azure_policy_index import PolicySearchIndex, format_rag_context, SearchResult
from foundry_client import FoundryRAGClient
from app.services.foundry_agent import FoundryAgentService, AgentRetrievalResult
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
    - 📋 QUICK ANSWER header
    - 📄 POLICY REFERENCE section with ASCII box
    - ⚠️ NOTICE footer
    - Citation lines at the end of quick answer

    Returns clean prose suitable for display in the Quick Answer UI box.
    """
    import re

    if not response_text:
        return ""

    text = response_text.strip()

    # If the response is already short (no formatting), return as-is
    if "📄 POLICY REFERENCE" not in text and "┌─" not in text:
        # Still strip the quick answer header if present
        text = re.sub(r'^📋\s*QUICK ANSWER\s*\n*', '', text, flags=re.IGNORECASE)
        return text.strip()

    # Extract text between "📋 QUICK ANSWER" and "📄 POLICY REFERENCE"
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


from promptflow.core import Prompty
from pathlib import Path

# Load Prompty from file
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "policytech.prompty"

class ChatService:
    def __init__(
        self,
        search_index: PolicySearchIndex,
        foundry_client: Optional[FoundryRAGClient] = None,
        foundry_agent_service: Optional[FoundryAgentService] = None
    ):
        self.search_index = search_index
        self.foundry_client = foundry_client
        self.foundry_agent_service = foundry_agent_service

        # Initialize synonym service for query expansion
        try:
            self.synonym_service = get_synonym_service()
            logger.info("Synonym service initialized for query expansion")
        except Exception as e:
            logger.warning(f"Synonym service unavailable: {e}")
            self.synonym_service = None

        # Load Prompty prompt
        try:
            self.prompty = Prompty.load(source=PROMPT_PATH)
        except Exception as e:
            logger.warning(f"Failed to load Prompty: {e}")
            self.prompty = None

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
        Process a chat message.
        """
        # Build safe filter expression
        filter_expr = build_applies_to_filter(request.filter_applies_to)

        # Try agentic retrieval first
        if self.foundry_agent_service:
             return await self._chat_with_agentic_retrieval(request, filter_expr)

        # Fall back to standard retrieval
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
        # Extract policy title from the formatted box
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

    async def _chat_with_agentic_retrieval(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """
        Handle chat using agentic retrieval (Foundry Agents).

        Uses a smart citation strategy:
        1. Agent generates synthesized answer with VECTOR_SEMANTIC_HYBRID search
        2. Extract policy references from agent's response (e.g., [Policy Name, Ref #XXXX])
        3. Search for those specific policies to get accurate evidence
        4. Fall back to query-based search only if no references found

        This ensures citations always match the answer content.
        """
        logger.info(f"Using agentic retrieval for query: {request.message[:50]}...")

        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        try:
            result: AgentRetrievalResult = await self.foundry_agent_service.retrieve(
                query=expanded_query,
                max_results=5
            )

            summary_text = result.synthesized_answer or NOT_FOUND_MESSAGE

            # For agentic retrieval, check if we got a meaningful answer
            found = summary_text and summary_text != NOT_FOUND_MESSAGE and summary_text != "No response generated."

            if not found:
                summary_text = NOT_FOUND_MESSAGE
                return ChatResponse(
                    response=summary_text,
                    summary=summary_text,
                    evidence=[],
                    raw_response=result.raw_response,
                    sources=[],
                    chunks_used=0,
                    found=False
                )

            # Check if agent declined to answer (unclear query, off-topic, etc.)
            # These responses should NOT include fallback evidence
            decline_patterns = [
                "I only answer RUSH policy questions",
                "Could you please rephrase",
                "I didn't understand that",
                "What RUSH policy topic can I help",
                "Please specify your question",
                "couldn't find",
                "could not find",
                "not in RUSH policies",
                "verify at https://rushumc.navexone.com",
            ]
            is_decline_response = any(
                pattern.lower() in summary_text.lower()
                for pattern in decline_patterns
            )

            if is_decline_response:
                logger.info("Agent declined to answer - not adding fallback evidence")
                return ChatResponse(
                    response=summary_text,
                    summary=summary_text,
                    evidence=[],
                    raw_response=result.raw_response,
                    sources=[],
                    chunks_used=0,
                    found=False
                )

            # Check if agent returned URL citations
            evidence_items = []
            sources = []

            if result.references:
                # Use agent's citations if available - these are verified from the agent's search
                evidence_items = [
                    EvidenceItem(
                        snippet=_truncate_verbatim(ref.content),
                        citation=ref.citation,
                        title=ref.title,
                        reference_number=ref.reference_number,
                        section=ref.section,
                        applies_to=ref.applies_to,
                        source_file=ref.source_file or None,
                        score=ref.score,
                        reranker_score=ref.reranker_score,
                        match_type="verified",  # Agent citations are direct search results
                    )
                    for ref in result.references
                ]
                sources = [{
                    "citation": ref.citation,
                    "source_file": ref.source_file,
                    "title": ref.title,
                    "reference_number": ref.reference_number,
                    "section": ref.section,
                    "applies_to": ref.applies_to,
                    "score": ref.score,
                    "match_type": "verified",
                } for ref in result.references]
            else:
                # Strategy: Extract policy refs from response, then search for them
                # Only use results that actually match the cited policy (by ref number or close title match)
                extracted_refs = self._extract_policy_refs_from_response(summary_text)

                if extracted_refs:
                    logger.info(f"Extracted {len(extracted_refs)} policy refs from response: {extracted_refs}")
                    all_results = []
                    seen_files = set()

                    for ref in extracted_refs[:3]:  # Limit to 3 policies
                        try:
                            found_match = False

                            # First, try to find by exact or partial reference number match
                            if ref['reference_number']:
                                ref_results = self.search_index.search(
                                    query=ref['reference_number'],
                                    top=5,
                                    filter_expr=filter_expr,
                                    use_semantic_ranking=True
                                )
                                for r in ref_results:
                                    # Check if reference number matches (exact or partial)
                                    if r.reference_number and (
                                        r.reference_number == ref['reference_number'] or
                                        ref['reference_number'] in r.reference_number or
                                        r.reference_number in ref['reference_number']
                                    ):
                                        if r.source_file and r.source_file not in seen_files:
                                            all_results.append(r)
                                            seen_files.add(r.source_file)
                                            found_match = True
                                            logger.info(f"Found exact ref match: {r.title} (Ref: {r.reference_number})")
                                            break

                            # If no ref match, try title with semantic ranking (only high confidence)
                            if not found_match and ref['title']:
                                ref_results = self.search_index.search(
                                    query=ref['title'],
                                    top=3,
                                    filter_expr=filter_expr,
                                    use_semantic_ranking=True
                                )
                                for r in ref_results:
                                    # Only accept if title is very similar (semantic reranker score > 2.5)
                                    # or title contains key words from the extracted title
                                    title_words = set(ref['title'].lower().split())
                                    result_words = set(r.title.lower().split())
                                    common_words = title_words & result_words
                                    # Need at least 2 common meaningful words or high reranker score
                                    meaningful_common = common_words - {'the', 'a', 'an', 'of', 'and', 'or', 'in', 'for', 'to'}
                                    if (len(meaningful_common) >= 2 or
                                        (r.reranker_score and r.reranker_score > 2.5)):
                                        if r.source_file and r.source_file not in seen_files:
                                            all_results.append(r)
                                            seen_files.add(r.source_file)
                                            found_match = True
                                            logger.info(f"Found title match: {r.title} (score: {r.reranker_score})")
                                            break

                        except Exception as e:
                            logger.warning(f"Failed to search for ref {ref}: {e}")

                    if all_results:
                        evidence_items = build_supporting_evidence(all_results, limit=3, match_type="verified")
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
                            "date_approved": r.date_approved,
                            "match_type": "verified"
                        } for r in all_results[:3]]
                        logger.info(f"Found {len(evidence_items)} verified matching policies for extracted refs")
                    else:
                        logger.info(f"Extracted refs not found in index: {extracted_refs}")

                # HALLUCINATION DETECTION:
                # If agent cited policies (extracted_refs) but NONE were found in index,
                # this is likely a hallucination. Return NOT_FOUND instead of showing fake answer.
                if extracted_refs and not evidence_items:
                    logger.warning(
                        f"HALLUCINATION DETECTED: Agent cited {len(extracted_refs)} policies "
                        f"({extracted_refs}) but NONE exist in the index. Returning NOT_FOUND."
                    )
                    return ChatResponse(
                        response=NOT_FOUND_MESSAGE,
                        summary=NOT_FOUND_MESSAGE,
                        evidence=[],
                        raw_response=result.raw_response,
                        sources=[],
                        chunks_used=0,
                        found=False
                    )

                # Fallback: If no refs extracted at all, use query-based search
                # This is for cases where agent didn't cite specific policies
                if not evidence_items and not extracted_refs:
                    logger.info("No extracted refs found, using expanded query search (related content)")
                    try:
                        # Use the original query + expanded terms for better relevance
                        search_results = self.search_index.search(
                            query=expanded_query,
                            top=10,  # Get more candidates for reranking
                            filter_expr=filter_expr,
                            use_semantic_ranking=True
                        )
                        if search_results:
                            # Deduplicate by source_file and take top 3
                            seen_files = set()
                            unique_results = []
                            for r in search_results:
                                if r.source_file and r.source_file not in seen_files:
                                    unique_results.append(r)
                                    seen_files.add(r.source_file)
                                if len(unique_results) >= 3:
                                    break

                            # Mark as "related" since these are fallback results, not exact matches
                            evidence_items = build_supporting_evidence(unique_results, limit=3, match_type="related")
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
                                "date_approved": r.date_approved,
                                "match_type": "related"
                            } for r in unique_results]
                            logger.info(f"Supplemented with {len(evidence_items)} related search results (fallback)")
                    except Exception as search_err:
                        logger.warning(f"Supplemental search failed: {search_err}")

            # Extract clean quick answer for display (strip RISEN formatting)
            clean_summary = _extract_quick_answer(summary_text)

            # Update found based on actual evidence (not stale value from line 402)
            actual_found = bool(evidence_items) and summary_text != NOT_FOUND_MESSAGE

            return ChatResponse(
                response=summary_text,  # Keep full response for reference
                summary=clean_summary,  # Clean version for Quick Answer UI
                evidence=evidence_items,
                raw_response=result.raw_response,
                sources=sources,
                chunks_used=len(evidence_items),
                found=actual_found
            )

        except Exception as e:
            logger.warning(f"Agentic retrieval failed, falling back to standard: {e}")
            return await self._chat_with_standard_retrieval(request, filter_expr)

    async def _chat_with_standard_retrieval(
        self,
        request: ChatRequest,
        filter_expr: Optional[str]
    ) -> ChatResponse:
        """Handle chat using standard hybrid search retrieval."""
        # Expand query with synonyms for better retrieval
        expanded_query, expansion = self._expand_query(request.message)

        try:
            search_results = self.search_index.search(
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

        summary_text = NOT_FOUND_MESSAGE if not search_results else ""
        raw_response_text = summary_text

        if self.foundry_client and self.foundry_client.is_configured:
            if search_results:
                # Use Prompty if available
                if self.prompty:
                    try:
                        # Prompty execution (synchronous usually, but we can wrap it if needed)
                        # Currently Prompty.load() returns a callable that handles the LLM call
                        # However, since we want to use our own client or custom logic, 
                        # we might just want the rendered messages. 
                        # For simplicity in this "native" refactor, we'll use the prompty object directly if supported,
                        # OR fallback to manual rendering if we need specific client control.
                        
                        # Azure AI Foundry "Prompty" library integrates with Azure OpenAI connections.
                        # If properly configured in the .prompty file (using env vars), it can run standalone.
                        
                        # Render messages using the template
                        # Note: Prompty libraries vary in API. Assuming we use it to render messages
                        # and then send to our client for consistency with existing pattern.
                        
                        # Render prompt to messages (this is a hypothetical standard API usage)
                        # Since standard Prompty usage is `result = prompty(context=..., question=...)`,
                        # we can try that first if we trust the env config in the prompty file.
                        
                        # For now, let's use our existing client but populate it with Prompty content 
                        # if we could extract it. But Prompty is designed to BE the runner.
                        
                        # Let's use the Prompty runner directly for the "Native" experience
                        response_text = self.prompty(
                            context=context,
                            question=request.message
                        )
                        summary_text = response_text or NOT_FOUND_MESSAGE
                        raw_response_text = response_text
                        
                    except Exception as e:
                        logger.warning(f"Prompty execution failed: {e}")
                        # Fallback to manual logic
                        summary_text = LLM_UNAVAILABLE_MESSAGE
                        raw_response_text = LLM_UNAVAILABLE_MESSAGE
                else:
                    # Legacy/Fallback logic
                    summary_instructions = f"""You are producing the QUICK ANSWER summary for a RUSH policy question.
Requirements:
- Provide 2-3 sentences that directly answer the user's question.
- Reference policy titles and reference numbers in parentheses when available (e.g., Medication Administration (MED-001)).
- Use ONLY the policy chunks provided below.
- If the information is not in the chunks, respond EXACTLY with: "{NOT_FOUND_MESSAGE}".
- Do not include bullet points or quote blocks. The supporting evidence will be shown separately.
"""
                    user_prompt = f"""{summary_instructions}

POLICY CHUNKS:
{context}

USER QUESTION: {request.message}
"""
                    try:
                        result = await self.foundry_client.chat_completion(
                            messages=[
                                {"role": "system", "content": RISEN_PROMPT},
                                {"role": "user", "content": user_prompt}
                            ],
                            temperature=0.1,
                            max_tokens=800,
                        )
                        response_text = result["content"].strip()
                        summary_text = response_text or NOT_FOUND_MESSAGE
                        raw_response_text = result["content"]
                    except Exception as chat_error:
                        logger.warning(f"Chat completion failed, using fallback: {chat_error}")
                        summary_text = LLM_UNAVAILABLE_MESSAGE
                        raw_response_text = LLM_UNAVAILABLE_MESSAGE
            else:
                summary_text = NOT_FOUND_MESSAGE
                raw_response_text = NOT_FOUND_MESSAGE
        else:
            summary_text = summary_text or (LLM_UNAVAILABLE_MESSAGE if search_results else NOT_FOUND_MESSAGE)
            raw_response_text = summary_text

        found = bool(search_results) and summary_text != NOT_FOUND_MESSAGE

        if not found:
            evidence_items = []
            sources = []

        return ChatResponse(
            response=summary_text,
            summary=summary_text,
            evidence=evidence_items,
            raw_response=raw_response_text,
            sources=sources,
            chunks_used=len(search_results),
            found=found
        )
