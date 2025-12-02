"""
Corrective RAG (cRAG) Service for Healthcare

Implements the Corrective RAG pattern which evaluates retrieval quality
BEFORE generation, enabling:
1. Detection of low-quality retrievals
2. Query decomposition for ambiguous cases
3. Re-retrieval when initial results are insufficient

This is critical for healthcare where generating from poor context
can lead to dangerous hallucinations.

Based on: Yan et al. 2024 - "Corrective Retrieval Augmented Generation"
"""

import logging
import re
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RetrievalQuality(Enum):
    """Classification of retrieved document quality."""
    RELEVANT = "relevant"      # High quality, proceed with generation
    AMBIGUOUS = "ambiguous"    # Uncertain, may need refinement
    IRRELEVANT = "irrelevant"  # Low quality, should not use


@dataclass
class QualityAssessment:
    """Assessment of a single retrieved document."""
    doc_index: int
    quality: RetrievalQuality
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class CorrectiveAction:
    """Action to take based on retrieval quality assessment."""
    action: str  # "proceed", "decompose", "refuse"
    relevant_docs: List[int]  # Indices of relevant docs to use
    sub_queries: List[str] = field(default_factory=list)  # For decomposition
    message: str = ""


class CorrectiveRAGService:
    """
    Corrective RAG service that evaluates retrieval quality before generation.
    
    Key features:
    1. Scores each retrieved document for relevance to query
    2. Classifies documents as relevant/ambiguous/irrelevant
    3. Decides whether to proceed, decompose query, or refuse
    4. Handles negation-aware relevance scoring
    """
    
    # Minimum relevant documents needed to proceed
    MIN_RELEVANT_DOCS = 2
    
    # Thresholds for quality classification
    RELEVANT_THRESHOLD = 0.6
    AMBIGUOUS_THRESHOLD = 0.3
    
    # Healthcare-specific terms that indicate high relevance
    HEALTHCARE_SIGNAL_TERMS = [
        "policy", "procedure", "protocol", "guideline", "requirement",
        "must", "shall", "required", "prohibited", "authorized",
        "rush", "rumc", "rumg", "roph", "patient", "staff", "nurse"
    ]
    
    # Negation terms for negation-aware scoring
    NEGATION_TERMS = ["not", "cannot", "never", "no", "prohibited", "forbidden", "don't", "doesn't"]

    def __init__(self):
        """Initialize the Corrective RAG service."""
        pass

    def assess_retrieval_quality(
        self,
        query: str,
        documents: List[Dict[str, Any]]
    ) -> List[QualityAssessment]:
        """
        Assess the quality of each retrieved document.
        
        Args:
            query: User's original query
            documents: List of retrieved documents with 'content', 'title', etc.
            
        Returns:
            List of QualityAssessment objects for each document
        """
        assessments = []
        query_lower = query.lower()
        query_terms = set(query_lower.split())
        query_has_negation = any(neg in query_lower for neg in self.NEGATION_TERMS)
        
        for i, doc in enumerate(documents):
            content = doc.get("content", "").lower()
            title = doc.get("title", "").lower()
            doc_text = f"{title} {content}"
            
            reasons = []
            
            # Score 1: Term overlap (0-0.4)
            term_matches = sum(1 for term in query_terms if term in doc_text and len(term) > 2)
            term_score = min(term_matches / max(len(query_terms), 1), 1.0) * 0.4
            
            # Score 2: Healthcare signal terms (0-0.2)
            signal_matches = sum(1 for term in self.HEALTHCARE_SIGNAL_TERMS if term in doc_text)
            signal_score = min(signal_matches / 5, 1.0) * 0.2
            
            # Score 3: Negation alignment (0-0.2)
            doc_has_negation = any(neg in doc_text for neg in self.NEGATION_TERMS)
            if query_has_negation == doc_has_negation:
                negation_score = 0.2
                reasons.append("Negation alignment: matched")
            elif query_has_negation and not doc_has_negation:
                negation_score = 0.05  # Query asks about prohibition, doc doesn't mention
                reasons.append("Negation mismatch: query has negation, doc doesn't")
            else:
                negation_score = 0.1
            
            # Score 4: Title relevance bonus (0-0.2)
            title_matches = sum(1 for term in query_terms if term in title and len(term) > 2)
            title_score = min(title_matches / max(len(query_terms), 1), 1.0) * 0.2
            if title_matches > 0:
                reasons.append(f"Title contains {title_matches} query terms")
            
            # Combined score
            total_score = term_score + signal_score + negation_score + title_score
            
            # Classify quality
            if total_score >= self.RELEVANT_THRESHOLD:
                quality = RetrievalQuality.RELEVANT
                reasons.append(f"High relevance score: {total_score:.2f}")
            elif total_score >= self.AMBIGUOUS_THRESHOLD:
                quality = RetrievalQuality.AMBIGUOUS
                reasons.append(f"Ambiguous relevance: {total_score:.2f}")
            else:
                quality = RetrievalQuality.IRRELEVANT
                reasons.append(f"Low relevance score: {total_score:.2f}")
            
            assessments.append(QualityAssessment(
                doc_index=i,
                quality=quality,
                score=total_score,
                reasons=reasons
            ))
        
        return assessments

    def determine_corrective_action(
        self,
        query: str,
        assessments: List[QualityAssessment]
    ) -> CorrectiveAction:
        """
        Determine what corrective action to take based on quality assessments.
        
        Args:
            query: Original user query
            assessments: Quality assessments for each document
            
        Returns:
            CorrectiveAction indicating what to do next
        """
        relevant = [a for a in assessments if a.quality == RetrievalQuality.RELEVANT]
        ambiguous = [a for a in assessments if a.quality == RetrievalQuality.AMBIGUOUS]
        
        # Case 1: Enough relevant documents - proceed with generation
        if len(relevant) >= self.MIN_RELEVANT_DOCS:
            logger.info(f"cRAG: {len(relevant)} relevant docs found - proceeding")
            return CorrectiveAction(
                action="proceed",
                relevant_docs=[a.doc_index for a in relevant],
                message=f"Found {len(relevant)} relevant documents"
            )
        
        # Case 2: Some relevant + ambiguous - proceed with caution
        if len(relevant) >= 1 and len(ambiguous) >= 1:
            logger.info(f"cRAG: {len(relevant)} relevant + {len(ambiguous)} ambiguous - proceeding with caution")
            # Use relevant docs + top ambiguous docs
            combined = relevant + sorted(ambiguous, key=lambda a: a.score, reverse=True)[:2]
            return CorrectiveAction(
                action="proceed",
                relevant_docs=[a.doc_index for a in combined],
                message=f"Found {len(relevant)} relevant + {len(ambiguous)} ambiguous documents"
            )
        
        # Case 3: Only ambiguous documents - try query decomposition
        if len(ambiguous) >= 2:
            sub_queries = self._decompose_query(query)
            if sub_queries:
                logger.info(f"cRAG: Only ambiguous docs - decomposing query into {len(sub_queries)} sub-queries")
                return CorrectiveAction(
                    action="decompose",
                    relevant_docs=[a.doc_index for a in ambiguous],
                    sub_queries=sub_queries,
                    message="Retrieval ambiguous - decomposing query"
                )
            else:
                # Can't decompose, proceed with ambiguous
                return CorrectiveAction(
                    action="proceed",
                    relevant_docs=[a.doc_index for a in ambiguous],
                    message="Using ambiguous documents (unable to decompose)"
                )
        
        # Case 4: Insufficient quality - refuse to generate
        logger.warning(f"cRAG: Insufficient retrieval quality - refusing to generate")
        return CorrectiveAction(
            action="refuse",
            relevant_docs=[],
            message="Unable to find sufficiently relevant policy documents"
        )

    def _decompose_query(self, query: str) -> List[str]:
        """
        Decompose a complex query into simpler sub-queries.
        
        Uses heuristics to split multi-part questions.
        """
        sub_queries = []
        query_lower = query.lower()
        
        # Pattern 1: "and" conjunction
        if " and " in query_lower:
            parts = re.split(r'\s+and\s+', query, flags=re.IGNORECASE)
            if len(parts) >= 2:
                # Reconstruct each part as a question
                for part in parts:
                    part = part.strip()
                    if not part.endswith("?"):
                        part = f"What is the policy regarding {part}?"
                    sub_queries.append(part)
        
        # Pattern 2: Multiple question words
        elif query_lower.count("what") + query_lower.count("how") + query_lower.count("when") >= 2:
            # Split on question words
            parts = re.split(r'(?i)(?=\b(?:what|how|when|who|where)\b)', query)
            for part in parts:
                part = part.strip()
                if len(part) > 10:  # Minimum meaningful length
                    if not part.endswith("?"):
                        part = part + "?"
                    sub_queries.append(part)
        
        # Pattern 3: "both" or "all" indicating multiple topics
        elif "both" in query_lower or "all" in query_lower:
            # Extract topics after "both" or "all"
            match = re.search(r'(?:both|all)\s+(?:the\s+)?(.+?)(?:\s+policies?)?(?:\?|$)', query, re.IGNORECASE)
            if match:
                topics = match.group(1)
                topic_list = re.split(r'\s+and\s+|\s*,\s*', topics)
                for topic in topic_list:
                    topic = topic.strip()
                    if len(topic) > 3:
                        sub_queries.append(f"What is the {topic} policy?")
        
        return sub_queries if len(sub_queries) >= 2 else []

    def filter_documents_by_quality(
        self,
        documents: List[Dict[str, Any]],
        assessments: List[QualityAssessment],
        action: CorrectiveAction
    ) -> List[Dict[str, Any]]:
        """
        Filter documents to only include those approved by quality assessment.
        
        Args:
            documents: Original list of documents
            assessments: Quality assessments
            action: Corrective action with approved doc indices
            
        Returns:
            Filtered list of documents
        """
        if not action.relevant_docs:
            return []
        
        # Sort by quality score descending
        sorted_indices = sorted(
            action.relevant_docs,
            key=lambda i: assessments[i].score if i < len(assessments) else 0,
            reverse=True
        )
        
        return [documents[i] for i in sorted_indices if i < len(documents)]


# Singleton instance
_corrective_rag_service: Optional[CorrectiveRAGService] = None


def get_corrective_rag_service() -> CorrectiveRAGService:
    """Get or create the Corrective RAG service singleton."""
    global _corrective_rag_service
    if _corrective_rag_service is None:
        _corrective_rag_service = CorrectiveRAGService()
    return _corrective_rag_service
