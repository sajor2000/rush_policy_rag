"""
Query Decomposition Service for Multi-Hop RAG

Decomposes complex queries into simpler sub-queries for better retrieval.
Critical for multi-policy questions that require information from multiple documents.

Based on: ACL 2025 - "Question Decomposition for Retrieval-Augmented Generation"
"""

import logging
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DecompositionResult:
    """Result of query decomposition."""
    needs_decomposition: bool
    original_query: str
    sub_queries: List[str]
    decomposition_type: str  # "comparison", "multi_topic", "conditional", "none"


class QueryDecomposer:
    """
    Decomposes complex queries into simpler sub-queries.
    
    Handles:
    - Comparison queries: "Compare X and Y policies"
    - Multi-topic queries: "What are the requirements for X and Y?"
    - Conditional queries: "If X happens, what about Y?"
    - Enumeration queries: "What are all the policies about X, Y, and Z?"
    """
    
    # Patterns indicating comparison queries
    COMPARISON_PATTERNS = [
        r'\bcompare\b',
        r'\bdifference\s+between\b',
        r'\bhow\s+(?:does|do)\s+.+\s+differ\b',
        r'\bvs\.?\b',
        r'\bversus\b',
        r'\bcontrast\b',
    ]
    
    # Patterns indicating multi-topic queries
    MULTI_TOPIC_PATTERNS = [
        r'\bboth\b',
        r'\ball\s+(?:the\s+)?(?:policies|procedures|requirements)\b',
        r'\bmultiple\b',
        r'\bdifferent\s+(?:policies|areas|topics)\b',
        r'\bacross\s+(?:policies|departments)\b',
    ]
    
    # Patterns indicating conditional/complex queries
    CONDITIONAL_PATTERNS = [
        r'\bif\s+.+\s+(?:then|what|does|do)\b',
        r'\bwhen\s+.+\s+(?:and|then|does)\b',
        r'\bin\s+(?:the\s+)?case\s+of\b',
    ]
    
    # Topic keywords for healthcare policies
    HEALTHCARE_TOPICS = {
        "verbal orders": ["verbal order", "verbal orders", "telephone order", "phone order"],
        "medication": ["medication", "drug", "pharmaceutical", "prescription", "med"],
        "hand-off": ["hand-off", "handoff", "shift change", "transfer", "sbar"],
        "safety": ["safety", "fall", "restraint", "suicide", "elopement", "precaution", "precautions"],
        "emergency": ["emergency", "emergencies", "code blue", "rapid response", "rrt", "code", "crisis"],
        "documentation": ["documentation", "charting", "epic", "record", "note", "document"],
        "identification": ["identification", "id", "patient id", "wristband", "armband", "patient identification"],
        "communication": ["communication", "report", "notify", "escalate", "alert", "method", "methods"],
        "infection": ["infection", "isolation", "ppe", "sterile", "hygiene"],
        "consent": ["consent", "informed consent", "authorization", "permission"],
    }
    
    def __init__(self):
        """Initialize the query decomposer."""
        # Compile patterns for efficiency
        self._comparison_re = [re.compile(p, re.IGNORECASE) for p in self.COMPARISON_PATTERNS]
        self._multi_topic_re = [re.compile(p, re.IGNORECASE) for p in self.MULTI_TOPIC_PATTERNS]
        self._conditional_re = [re.compile(p, re.IGNORECASE) for p in self.CONDITIONAL_PATTERNS]
    
    def needs_decomposition(self, query: str) -> Tuple[bool, str]:
        """
        Determine if a query needs decomposition.
        
        Returns:
            Tuple of (needs_decomposition, decomposition_type)
        """
        query_lower = query.lower()
        
        # Check for comparison patterns
        for pattern in self._comparison_re:
            if pattern.search(query):
                return True, "comparison"
        
        # Check for multi-topic patterns
        for pattern in self._multi_topic_re:
            if pattern.search(query):
                return True, "multi_topic"
        
        # Check for conditional patterns
        for pattern in self._conditional_re:
            if pattern.search(query):
                return True, "conditional"
        
        # Check for multiple healthcare topics mentioned
        topics_found = []
        for topic_name, keywords in self.HEALTHCARE_TOPICS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    topics_found.append(topic_name)
                    break
        
        if len(set(topics_found)) >= 2:
            return True, "multi_topic"
        
        # Check for "and" connecting policy-related nouns
        and_pattern = r'\b(?:policy|procedure|protocol|requirement|guideline).*\band\b.*(?:policy|procedure|protocol|requirement|guideline)'
        if re.search(and_pattern, query_lower):
            return True, "multi_topic"
        
        return False, "none"
    
    def decompose(self, query: str) -> DecompositionResult:
        """
        Decompose a complex query into simpler sub-queries.
        
        Args:
            query: The original complex query
            
        Returns:
            DecompositionResult with sub-queries
        """
        needs_decomp, decomp_type = self.needs_decomposition(query)
        
        if not needs_decomp:
            return DecompositionResult(
                needs_decomposition=False,
                original_query=query,
                sub_queries=[query],
                decomposition_type="none"
            )
        
        sub_queries = []
        
        if decomp_type == "comparison":
            sub_queries = self._decompose_comparison(query)
        elif decomp_type == "multi_topic":
            sub_queries = self._decompose_multi_topic(query)
        elif decomp_type == "conditional":
            sub_queries = self._decompose_conditional(query)
        
        # Fallback: if decomposition failed, use original
        if not sub_queries:
            sub_queries = [query]
        
        logger.info(f"Decomposed query into {len(sub_queries)} sub-queries: {sub_queries}")
        
        return DecompositionResult(
            needs_decomposition=True,
            original_query=query,
            sub_queries=sub_queries,
            decomposition_type=decomp_type
        )
    
    def _decompose_comparison(self, query: str) -> List[str]:
        """Decompose comparison queries."""
        sub_queries = []
        query_lower = query.lower()
        
        # Extract items being compared
        # Pattern: "Compare X and Y" or "X vs Y" or "difference between X and Y"
        
        # Try "X and Y" pattern
        and_match = re.search(
            r'(?:compare|difference\s+between|contrast)\s+(?:the\s+)?(.+?)\s+(?:and|vs\.?|versus)\s+(?:the\s+)?(.+?)(?:\s+(?:policies|procedures|requirements))?(?:\?|$)',
            query_lower
        )
        
        if and_match:
            item1 = and_match.group(1).strip()
            item2 = and_match.group(2).strip()
            
            # Clean up items
            item1 = re.sub(r'\s+(?:policy|procedure|protocol)$', '', item1)
            item2 = re.sub(r'\s+(?:policy|procedure|protocol)$', '', item2)
            
            sub_queries.append(f"What is the {item1} policy at RUSH?")
            sub_queries.append(f"What is the {item2} policy at RUSH?")
            sub_queries.append(f"What are the key requirements of the {item1} policy?")
            sub_queries.append(f"What are the key requirements of the {item2} policy?")
        
        # If no pattern matched, try extracting topics
        if not sub_queries:
            topics = self._extract_topics(query)
            for topic in topics[:3]:  # Limit to 3 topics
                sub_queries.append(f"What is the RUSH policy on {topic}?")
        
        return sub_queries
    
    def _decompose_multi_topic(self, query: str) -> List[str]:
        """Decompose multi-topic queries."""
        sub_queries = []
        
        # Extract all topics mentioned
        topics = self._extract_topics(query)
        
        if topics:
            for topic in topics[:4]:  # Limit to 4 topics
                sub_queries.append(f"What is the RUSH policy on {topic}?")
        
        # Also add a general query about the main subject
        # Extract the main action/requirement being asked about
        action_match = re.search(
            r'(?:what|how|when|who).+?(?:required|requirement|method|procedure|protocol|documentation)',
            query.lower()
        )
        if action_match:
            sub_queries.append(query)  # Keep original as one sub-query
        
        return sub_queries
    
    def _decompose_conditional(self, query: str) -> List[str]:
        """Decompose conditional queries."""
        sub_queries = []
        query_lower = query.lower()
        
        # Pattern: "If X happens, what about Y?"
        if_match = re.search(r'if\s+(.+?),?\s+(?:then\s+)?(?:what|how|does|do)\s+(.+)', query_lower)
        
        if if_match:
            condition = if_match.group(1).strip()
            question = if_match.group(2).strip()
            
            # Create sub-queries for both parts
            sub_queries.append(f"What is the RUSH policy when {condition}?")
            sub_queries.append(f"What is the RUSH policy regarding {question}?")
        
        # Also extract topics
        topics = self._extract_topics(query)
        for topic in topics[:2]:
            sub_queries.append(f"What is the RUSH policy on {topic}?")
        
        return sub_queries
    
    def _extract_topics(self, query: str) -> List[str]:
        """Extract healthcare topics from a query."""
        query_lower = query.lower()
        topics_found = []
        
        for topic_name, keywords in self.HEALTHCARE_TOPICS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    topics_found.append(topic_name)
                    break
        
        return list(set(topics_found))
    
    def merge_results(self, sub_results: List[List[dict]]) -> List[dict]:
        """
        Merge and deduplicate results from multiple sub-queries.
        
        Args:
            sub_results: List of result lists from each sub-query
            
        Returns:
            Merged and deduplicated list of documents
        """
        seen_ids = set()
        merged = []
        
        # Interleave results to get diversity
        max_len = max(len(r) for r in sub_results) if sub_results else 0
        
        for i in range(max_len):
            for results in sub_results:
                if i < len(results):
                    doc = results[i]
                    doc_id = doc.get('id') or doc.get('reference_number') or doc.get('content', '')[:100]
                    
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        merged.append(doc)
        
        return merged


# Singleton instance
_query_decomposer: Optional[QueryDecomposer] = None


def get_query_decomposer() -> QueryDecomposer:
    """Get or create the QueryDecomposer singleton."""
    global _query_decomposer
    if _query_decomposer is None:
        _query_decomposer = QueryDecomposer()
    return _query_decomposer
