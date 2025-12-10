"""
Instance Search Service - Find relevant sections within a specific policy.

This service supports TWO search modes:

1. EXACT TERM SEARCH: Find all occurrences of a specific word/phrase
   - Example: "employee" → finds every instance of "employee" in the policy

2. SEMANTIC SECTION SEARCH: Find sections about a topic (phrase/concept search)
   - Example: "employee access to records" → finds sections discussing this topic
   - Uses Azure AI Search semantic ranking within the filtered policy

This enables users to ask questions like:
- "show me where employee is mentioned in HIPAA policy" (exact)
- "find the section about employee access to their own records" (semantic)
- "where does it discuss training requirements" (semantic)
"""

import re
import logging
from typing import List, Optional
from azure.search.documents import SearchClient
from app.models.schemas import TermInstance, InstanceSearchResponse

logger = logging.getLogger(__name__)

CONTEXT_WINDOW = 100  # Characters before/after match to include
SEMANTIC_TOP_K = 20   # Number of chunks to return for semantic search


class InstanceSearchService:
    """Service for finding term instances or relevant sections within a specific policy."""

    def __init__(self, search_client: SearchClient, embedding_function=None):
        self.search_client = search_client
        self.embedding_function = embedding_function  # For semantic search

    def search_instances(
        self,
        policy_ref: str,
        search_term: str,
        case_sensitive: bool = False,
        semantic_search: bool = False
    ) -> InstanceSearchResponse:
        """
        Find instances or sections within a specific policy.

        Args:
            policy_ref: Policy reference number to search within
            search_term: Term or phrase to find
            case_sensitive: Whether to use case-sensitive matching (exact mode only)
            semantic_search: If True, use semantic ranking for topic/concept search

        Returns:
            InstanceSearchResponse with found instances/sections
        """
        # Step 1: Retrieve chunks for this policy
        if semantic_search:
            chunks = self._get_policy_chunks_semantic(policy_ref, search_term)
        else:
            chunks = self._get_policy_chunks(policy_ref)

        if not chunks:
            logger.info(f"No chunks found for policy ref '{policy_ref}'")
            return InstanceSearchResponse(
                policy_title="",
                policy_ref=policy_ref,
                search_term=search_term,
                total_instances=0,
                instances=[],
                source_file=None
            )

        # Get policy metadata from first chunk
        policy_title = chunks[0].get("title", "")
        source_file = chunks[0].get("source_file", "")

        # Step 2: Process chunks based on search mode
        instances: List[TermInstance] = []

        if semantic_search:
            # For semantic search, each chunk IS a relevant section
            for chunk in chunks:
                instance = self._chunk_to_instance(chunk, search_term)
                instances.append(instance)
        else:
            # For exact search, find all term matches in each chunk
            for chunk in chunks:
                chunk_instances = self._find_instances_in_chunk(
                    chunk, search_term, case_sensitive
                )
                instances.extend(chunk_instances)

        # Step 3: Sort by page number, then by position
        instances.sort(key=lambda x: (x.page_number or 0, x.position))

        logger.info(
            f"Instance search ({'semantic' if semantic_search else 'exact'}): "
            f"'{search_term}' in policy '{policy_ref}' "
            f"-> {len(instances)} results across {len(chunks)} chunks"
        )

        return InstanceSearchResponse(
            policy_title=policy_title,
            policy_ref=policy_ref,
            search_term=search_term,
            total_instances=len(instances),
            instances=instances,
            source_file=source_file
        )

    def search_within_policy(
        self,
        policy_ref: str,
        query: str
    ) -> InstanceSearchResponse:
        """
        Smart search that auto-detects whether to use exact or semantic search.

        - Single words or short exact phrases → exact match
        - Longer phrases or questions → semantic search

        Args:
            policy_ref: Policy reference number
            query: User's search query

        Returns:
            InstanceSearchResponse with results
        """
        # Heuristic: if query is 1-2 words and looks like a term, use exact search
        # Otherwise, use semantic search for topic/concept matching
        words = query.strip().split()
        is_short_term = len(words) <= 2 and len(query) <= 30

        # Check if it's a question (semantic) or term lookup (exact)
        question_words = {"what", "where", "how", "when", "why", "which", "does", "is", "are", "can"}
        starts_with_question = words[0].lower() in question_words if words else False

        use_semantic = not is_short_term or starts_with_question

        logger.info(
            f"Smart search mode: {'semantic' if use_semantic else 'exact'} "
            f"for query '{query}' (words={len(words)}, question={starts_with_question})"
        )

        return self.search_instances(
            policy_ref=policy_ref,
            search_term=query,
            case_sensitive=False,
            semantic_search=use_semantic
        )

    def _get_policy_chunks(self, policy_ref: str) -> List[dict]:
        """Retrieve all chunks for a specific policy using Azure Search filter."""
        # Escape single quotes for OData filter (OData uses '' to escape ')
        safe_ref = policy_ref.replace("'", "''")

        # Use search with filter to get all chunks - filter is O(1) on indexed field
        # Note: page_number may not exist in all index versions, so we don't include it in select
        # and don't order by it - we'll sort by chunk_index instead if available
        results = list(self.search_client.search(
            search_text="*",
            filter=f"reference_number eq '{safe_ref}'",
            select=[
                "id", "content", "title", "section",
                "source_file", "reference_number", "chunk_index"
            ],
            top=1000,  # Get all chunks (most policies have <100 chunks)
            order_by=["chunk_index asc"]
        ))

        logger.debug(f"Retrieved {len(results)} chunks for policy ref '{policy_ref}'")
        return results

    def _find_instances_in_chunk(
        self,
        chunk: dict,
        search_term: str,
        case_sensitive: bool
    ) -> List[TermInstance]:
        """Find all instances of a term within a single chunk."""
        content = chunk.get("content", "")
        if not content:
            return []

        # Build regex pattern that matches the term and common variations
        # Use word boundary at start but allow word endings (plurals, possessives)
        flags = 0 if case_sensitive else re.IGNORECASE
        escaped_term = re.escape(search_term)
        # Match: employee, employees, employee's, etc.
        pattern = rf'\b{escaped_term}(s|\'s|es|ed|ing)?\b'

        instances = []
        for match in re.finditer(pattern, content, flags):
            # Extract context around the match
            context_start = max(0, match.start() - CONTEXT_WINDOW)
            context_end = min(len(content), match.end() + CONTEXT_WINDOW)
            context = content[context_start:context_end]

            # Calculate highlight positions within context string
            highlight_start = match.start() - context_start
            highlight_end = match.end() - context_start

            # Add ellipsis if context is truncated
            if context_start > 0:
                context = "..." + context
                highlight_start += 3
                highlight_end += 3
            if context_end < len(content):
                context = context + "..."

            # Parse section info from "X. Title" format
            section = chunk.get("section", "")
            section_parts = section.split(". ", 1) if section else ["", ""]
            section_number = section_parts[0] if len(section_parts) > 0 else ""
            section_title = section_parts[1] if len(section_parts) > 1 else ""

            # page_number may not exist in older index versions
            # Estimate from chunk_index: assuming ~2 chunks per page
            page_num = chunk.get("page_number")
            if page_num is None:
                chunk_idx = chunk.get("chunk_index", 0)
                page_num = max(1, (chunk_idx // 2) + 1)  # Rough estimate

            instances.append(TermInstance(
                page_number=page_num,
                section=section_number,
                section_title=section_title,
                context=context,
                position=match.start(),
                chunk_id=chunk.get("id", ""),
                highlight_start=highlight_start,
                highlight_end=highlight_end
            ))

        return instances

    def _get_policy_chunks_semantic(self, policy_ref: str, query: str) -> List[dict]:
        """
        Retrieve relevant chunks for a policy using semantic search.

        This filters to a specific policy AND ranks chunks by relevance to the query.
        Perfect for "find the section about X in policy Y" questions.
        """
        safe_ref = policy_ref.replace("'", "''")

        # Use semantic hybrid search within the filtered policy
        # This combines keyword matching with Azure's semantic ranker
        # Note: page_number may not exist in older index versions
        results = list(self.search_client.search(
            search_text=query,
            filter=f"reference_number eq '{safe_ref}'",
            query_type="semantic",
            semantic_configuration_name="default-semantic",
            select=[
                "id", "content", "title", "section",
                "source_file", "reference_number", "chunk_index"
            ],
            top=SEMANTIC_TOP_K,
            include_total_count=True
        ))

        logger.debug(
            f"Semantic search in policy '{policy_ref}' for '{query}' "
            f"-> {len(results)} relevant chunks"
        )
        return results

    def _chunk_to_instance(self, chunk: dict, search_term: str) -> TermInstance:
        """
        Convert a semantically-matched chunk to a TermInstance.

        For semantic search, we show the chunk content as the context
        and try to highlight any matching terms if present.
        """
        content = chunk.get("content", "")

        # Truncate content for display (first 300 chars)
        display_content = content[:300]
        if len(content) > 300:
            display_content += "..."

        # Try to find and highlight the search term if present
        highlight_start = 0
        highlight_end = 0

        # Search for any word from the query in the content (case-insensitive)
        query_words = search_term.lower().split()
        content_lower = display_content.lower()

        for word in query_words:
            if len(word) >= 3:  # Only highlight words with 3+ chars
                pos = content_lower.find(word)
                if pos != -1:
                    highlight_start = pos
                    highlight_end = pos + len(word)
                    break

        # Parse section info
        section = chunk.get("section", "")
        section_parts = section.split(". ", 1) if section else ["", ""]
        section_number = section_parts[0] if len(section_parts) > 0 else ""
        section_title = section_parts[1] if len(section_parts) > 1 else ""

        # page_number may not exist in older index versions
        # Estimate from chunk_index: assuming ~2 chunks per page
        page_num = chunk.get("page_number")
        if page_num is None:
            chunk_idx = chunk.get("chunk_index", 0)
            page_num = max(1, (chunk_idx // 2) + 1)  # Rough estimate

        return TermInstance(
            page_number=page_num,
            section=section_number,
            section_title=section_title,
            context=display_content,
            position=0,  # Semantic matches don't have exact positions
            chunk_id=chunk.get("id", ""),
            highlight_start=highlight_start,
            highlight_end=highlight_end
        )


# Singleton pattern for service reuse
_instance_search_service: Optional[InstanceSearchService] = None


def get_instance_search_service(search_client: SearchClient, embedding_function=None) -> InstanceSearchService:
    """Get or create the instance search service singleton."""
    global _instance_search_service
    if _instance_search_service is None:
        _instance_search_service = InstanceSearchService(search_client, embedding_function)
    return _instance_search_service
