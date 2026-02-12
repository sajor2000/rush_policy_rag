"""
Context Expander Service for RAG Pipeline.

Expands retrieved chunks with parent and sibling context to provide
complete procedural information to the LLM. This addresses the "lost in middle"
problem by giving coherent policy sections instead of isolated fragments.

Architecture:
    Query → Azure Search → Cohere Rerank → Top N → EXPAND CONTEXT → LLM
                                                    ↓
                                          For each hit:
                                          1. Find current chunk's index
                                          2. Fetch sibling chunks (chunk_index ± 1)
                                          3. Merge into coherent context blocks

Why This Helps:
- Individual chunks may lack procedural context that spans sections
- Sibling chunks contain continuation of procedures
- LLM sees coherent policy sections, not fragments
- Reduces "lost in middle" by providing complete context

Performance:
- Uses existing indexed fields (chunk_index, source_file)
- No re-indexing required
- Deduplicates overlapping expansions
"""

import logging
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field

from app.services.cohere_rerank_service import RerankResult
from app.core.security import escape_odata_string

logger = logging.getLogger(__name__)


@dataclass
class ExpandedContext:
    """
    Represents a chunk with its expanded context.

    Contains the original chunk plus any sibling content
    for complete procedural context.
    """
    # Original hit information
    original: RerankResult

    # Expanded content (merged from siblings + self)
    expanded_content: str = ""

    # Context chain for debugging/transparency
    context_chain: List[str] = field(default_factory=list)

    # Tracking
    parent_included: bool = False
    siblings_included: int = 0

    @property
    def content_for_rag(self) -> str:
        """Return expanded content or original if no expansion."""
        return self.expanded_content or self.original.content


class ContextExpander:
    """
    Expands retrieved chunks with sibling context.

    Uses existing Azure AI Search indexed fields:
    - chunk_index: Position in document sequence
    - source_file: Groups chunks from same document

    The expansion fetches adjacent chunks (chunk_index ± 1) to provide
    complete procedural context for the LLM.
    """

    def __init__(
        self,
        search_index,
        include_parent: bool = True,
        include_siblings: bool = True,
        max_siblings: int = 1
    ):
        """
        Initialize context expander.

        Args:
            search_index: PolicySearchIndex instance for fetching chunks
            include_parent: Whether to fetch parent chunks (not yet implemented)
            include_siblings: Whether to fetch adjacent chunks
            max_siblings: Max sibling chunks on each side (1 = ±1 = 2 total)
        """
        self.search_index = search_index
        self.include_parent = include_parent
        self.include_siblings = include_siblings
        self.max_siblings = max_siblings
        self._supports_chunk_index_filter = self._detect_chunk_index_filter_support()

        if self.include_siblings and not self._supports_chunk_index_filter:
            self.include_siblings = False
            logger.warning(
                "Context expansion sibling mode disabled: live index does not support "
                "filtering on chunk_index. Continuing without sibling fetch."
            )

    def _detect_chunk_index_filter_support(self) -> bool:
        """
        Check whether chunk_index is filterable in the active Azure Search index.
        """
        try:
            index_name = getattr(self.search_index, "index_name", "")
            index_client = getattr(self.search_index, "index_client", None)
            if not index_name or index_client is None:
                return False

            index = index_client.get_index(index_name)
            for field in index.fields:
                if field.name == "chunk_index":
                    return bool(getattr(field, "filterable", False))
        except Exception as e:
            logger.warning(f"Unable to inspect chunk_index filter capability: {e}")
        return False

    async def expand_context(
        self,
        reranked: List[RerankResult],
        top_n: int = 3
    ) -> List[ExpandedContext]:
        """
        Expand context for top N reranked results.

        For each top result:
        1. Find the chunk's index in the document
        2. Fetch sibling chunks (chunk_index ± max_siblings)
        3. Merge into coherent expanded content

        Args:
            reranked: List of RerankResult from Cohere rerank
            top_n: Number of top results to expand

        Returns:
            List of ExpandedContext with merged content
        """
        if not reranked:
            return []

        # Only expand top N results to control context size
        to_expand = reranked[:top_n]

        # Build expanded contexts
        expanded_results = []
        seen_content: Set[str] = set()  # Deduplicate content

        for rr in to_expand:
            expanded = await self._expand_single_result(rr, seen_content)
            expanded_results.append(expanded)

        # Log expansion statistics
        total_original = sum(len(e.original.content) for e in expanded_results)
        total_expanded = sum(len(e.expanded_content) for e in expanded_results)
        expansion_ratio = total_expanded / total_original if total_original > 0 else 1.0
        siblings_found = sum(e.siblings_included for e in expanded_results)

        logger.info(
            f"Context expansion: {len(expanded_results)} chunks | "
            f"Siblings found: {siblings_found} | "
            f"Original: {total_original} chars → Expanded: {total_expanded} chars "
            f"({expansion_ratio:.1f}x)"
        )

        return expanded_results

    async def _expand_single_result(
        self,
        rr: RerankResult,
        seen_content: Set[str]
    ) -> ExpandedContext:
        """
        Expand a single reranked result with sibling context.

        Args:
            rr: RerankResult to expand
            seen_content: Set of already-seen content hashes (for deduplication)

        Returns:
            ExpandedContext with merged content
        """
        context_parts = []
        context_chain = []
        siblings_included = 0

        # Hash original content to track what we've seen
        content_hash = hash(rr.content[:100]) if rr.content else 0
        seen_content.add(content_hash)

        current_chunk_index = None
        if self.include_siblings and self.max_siblings > 0:
            # Step 1: Find current chunk's index by searching for matching content
            current_chunk_index = await self._find_chunk_index(rr.source_file, rr.content)

        if current_chunk_index is not None and self.include_siblings and self.max_siblings > 0:
            # Step 2: Fetch previous sibling(s)
            for offset in range(self.max_siblings, 0, -1):
                prev_index = current_chunk_index - offset
                if prev_index >= 0:
                    prev_chunk = await self._fetch_chunk_by_index(
                        rr.source_file, prev_index, rr.title
                    )
                    if prev_chunk:
                        sib_hash = hash(prev_chunk["content"][:100]) if prev_chunk.get("content") else 0
                        if sib_hash not in seen_content:
                            context_parts.append(prev_chunk["content"])
                            context_chain.append(f"sibling_before[idx={prev_index}]:{prev_chunk.get('section', 'N/A')}")
                            seen_content.add(sib_hash)
                            siblings_included += 1

        # Step 3: Add original content
        context_parts.append(rr.content)
        context_chain.append(f"original[idx={current_chunk_index}]:{rr.section}")

        if current_chunk_index is not None and self.include_siblings and self.max_siblings > 0:
            # Step 4: Fetch next sibling(s)
            for offset in range(1, self.max_siblings + 1):
                next_index = current_chunk_index + offset
                next_chunk = await self._fetch_chunk_by_index(
                    rr.source_file, next_index, rr.title
                )
                if next_chunk:
                    sib_hash = hash(next_chunk["content"][:100]) if next_chunk.get("content") else 0
                    if sib_hash not in seen_content:
                        context_parts.append(next_chunk["content"])
                        context_chain.append(f"sibling_after[idx={next_index}]:{next_chunk.get('section', 'N/A')}")
                        seen_content.add(sib_hash)
                        siblings_included += 1

        # Merge context parts with clear separation
        if len(context_parts) > 1:
            expanded_content = "\n\n---\n\n".join(context_parts)
        else:
            expanded_content = context_parts[0] if context_parts else rr.content

        return ExpandedContext(
            original=rr,
            expanded_content=expanded_content,
            context_chain=context_chain,
            parent_included=False,
            siblings_included=siblings_included
        )

    async def _find_chunk_index(
        self,
        source_file: str,
        content: str
    ) -> Optional[int]:
        """
        Find the chunk_index for a chunk by matching content.

        Args:
            source_file: Source PDF filename
            content: Content to match

        Returns:
            chunk_index if found, None otherwise
        """
        if not source_file or not content:
            return None

        try:
            # Search for chunks from this document
            safe_source = escape_odata_string(source_file)
            filter_expr = f"source_file eq '{safe_source}'"

            # Use a content prefix for matching (first 200 chars to avoid edge cases)
            content_prefix = content[:200].strip()

            results = self.search_index.search_client.search(
                search_text=content_prefix,
                filter=filter_expr,
                select=["chunk_index", "content"],
                top=10  # Get a few candidates
            )

            for result in results:
                # Match by content prefix
                result_content = result.get("content", "")
                if result_content and result_content[:200].strip() == content_prefix:
                    chunk_index = result.get("chunk_index")
                    if chunk_index is not None:
                        logger.debug(f"Found chunk_index={chunk_index} for {source_file}")
                        return chunk_index

            logger.debug(f"Could not find chunk_index for {source_file}")
            return None

        except Exception as e:
            logger.warning(f"Error finding chunk_index for {source_file}: {e}")
            return None

    async def _fetch_chunk_by_index(
        self,
        source_file: str,
        chunk_index: int,
        expected_title: str
    ) -> Optional[Dict]:
        """
        Fetch a specific chunk by source_file and chunk_index.

        Args:
            source_file: Source PDF filename
            chunk_index: Chunk index to fetch
            expected_title: Expected policy title (for validation)

        Returns:
            Dict with chunk data if found, None otherwise
        """
        if not source_file:
            return None

        try:
            safe_source = escape_odata_string(source_file)
            filter_expr = f"source_file eq '{safe_source}' and chunk_index eq {int(chunk_index)}"

            results = self.search_index.search_client.search(
                search_text="*",
                filter=filter_expr,
                select=["content", "section", "title", "chunk_index"],
                top=1
            )

            for result in results:
                # Validate same policy title
                if result.get("title") == expected_title:
                    return {
                        "content": result.get("content", ""),
                        "section": result.get("section", ""),
                        "chunk_index": result.get("chunk_index", 0)
                    }

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch chunk at index {chunk_index} for {source_file}: {e}")
            return None


def build_expanded_rag_context(
    expanded_contexts: List[ExpandedContext],
    max_chunks: int = 5
) -> str:
    """
    Build RAG context string from expanded contexts.

    Formats expanded contexts for LLM prompt with clear attribution
    and relevance indicators.

    Args:
        expanded_contexts: List of ExpandedContext objects
        max_chunks: Maximum chunks to include

    Returns:
        Formatted context string for RAG prompt
    """
    if not expanded_contexts:
        return "No relevant policy documents found."

    context_parts = []
    for i, ctx in enumerate(expanded_contexts[:max_chunks], 1):
        rr = ctx.original

        # Show expansion info if context was expanded
        expansion_note = ""
        if ctx.siblings_included > 0 or ctx.parent_included:
            expansions = []
            if ctx.parent_included:
                expansions.append("parent")
            if ctx.siblings_included > 0:
                expansions.append(f"{ctx.siblings_included} siblings")
            expansion_note = f" [Expanded: {', '.join(expansions)}]"

        context_parts.append(f"""
═══════════════════════════════════════════════════════════════
 POLICY CHUNK {i} (Relevance: {rr.cohere_score:.2f}){expansion_note}
═══════════════════════════════════════════════════════════════
┌────────────────────────────────────────────────────────────┐
│ POLICY: {rr.title}
│ Reference: {rr.reference_number}
│ Section: {rr.section}
│ Applies To: {rr.applies_to}
│ Source: {rr.source_file}
└────────────────────────────────────────────────────────────┘

{ctx.content_for_rag}
""")

    return "\n".join(context_parts)
