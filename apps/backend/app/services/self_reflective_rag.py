"""
Self-Reflective RAG Service for Healthcare

Implements the SELF-RAG pattern where the LLM critiques its own response
before returning to the user. Critical for healthcare where unchecked
responses could contain dangerous inaccuracies.

Key features:
1. Critique loop - LLM evaluates its own response for grounding
2. Regeneration with stricter prompts if critique fails
3. Tracks which claims are supported vs unsupported

Based on: Asai et al. 2024 - "Self-RAG: Learning to Retrieve, Generate, and Critique"
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CritiqueResult:
    """Result of self-critique on a response."""
    is_supported: bool
    is_relevant: bool
    is_grounded: bool
    overall_pass: bool
    issues: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    confidence: float = 0.0


class SelfReflectiveRAGService:
    """
    Self-Reflective RAG service that critiques responses before returning.
    
    Uses a lightweight critique approach that doesn't require an additional
    LLM call - instead uses heuristics and pattern matching to identify
    potential issues with grounding and relevance.
    """
    
    # Patterns indicating unsupported claims
    UNSUPPORTED_CLAIM_PATTERNS = [
        r'\bgenerally\b',
        r'\btypically\b',
        r'\busually\b',
        r'\boften\b',
        r'\bmost\s+(?:hospitals?|facilities|organizations?)\b',
        r'\bindustry\s+standard\b',
        r'\bbest\s+practice\b',
        r'\bcommonly\b',
    ]
    
    # Patterns indicating speculation
    SPECULATION_PATTERNS = [
        r'\bi\s+(?:think|believe|assume)\b',
        r'\bprobably\b',
        r'\blikely\b',
        r'\bmight\b',
        r'\bcould\s+be\b',
        r'\bpossibly\b',
        r'\bperhaps\b',
        r'\bseems?\s+(?:like|to)\b',
        r'\bappears?\s+to\b',
        r'\bmy\s+understanding\b',
    ]
    
    # Patterns indicating proper grounding
    GROUNDING_PATTERNS = [
        r'Ref\s*#?\s*\d+',
        r'according\s+to\s+(?:the\s+)?policy',
        r'(?:the\s+)?policy\s+states',
        r'per\s+(?:the\s+)?(?:policy|guidelines?)',
        r'\(Ref\s*#?\s*\d+\)',
    ]
    
    # Healthcare-specific terms that should appear in grounded responses
    HEALTHCARE_GROUNDING_TERMS = [
        "rush", "rumc", "policy", "procedure", "protocol",
        "ref", "reference", "section"
    ]

    def __init__(self):
        """Initialize the Self-Reflective RAG service."""
        pass

    def critique_response(
        self,
        response: str,
        query: str,
        contexts: List[str]
    ) -> CritiqueResult:
        """
        Critique a generated response for grounding and relevance.
        
        Args:
            response: Generated response to critique
            query: Original user query
            contexts: Retrieved context passages
            
        Returns:
            CritiqueResult with assessment details
        """
        response_lower = response.lower()
        query_lower = query.lower()
        combined_context = " ".join(contexts).lower()
        
        issues = []
        unsupported_claims = []
        
        # Check 1: Is response relevant to query?
        query_terms = set(word for word in query_lower.split() if len(word) > 3)
        response_terms = set(word for word in response_lower.split() if len(word) > 3)
        term_overlap = len(query_terms & response_terms) / max(len(query_terms), 1)
        is_relevant = term_overlap >= 0.3
        
        if not is_relevant:
            issues.append(f"Low query-response relevance: {term_overlap:.1%} term overlap")
        
        # Check 2: Are claims grounded in context?
        has_grounding = any(
            re.search(pattern, response, re.IGNORECASE)
            for pattern in self.GROUNDING_PATTERNS
        )
        
        if not has_grounding:
            issues.append("No citation or policy reference found in response")
        
        # Check grounding terms
        grounding_term_count = sum(
            1 for term in self.HEALTHCARE_GROUNDING_TERMS 
            if term in response_lower
        )
        is_grounded = has_grounding and grounding_term_count >= 2
        
        if grounding_term_count < 2:
            issues.append(f"Low healthcare grounding: only {grounding_term_count} grounding terms")
        
        # Check 3: Are there unsupported claims?
        for pattern in self.UNSUPPORTED_CLAIM_PATTERNS:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                # Check if this phrase appears in context
                if match.lower() not in combined_context:
                    unsupported_claims.append(match)
        
        if unsupported_claims:
            issues.append(f"Potentially unsupported generalizations: {unsupported_claims[:3]}")
        
        # Check 4: Is there speculation?
        speculation_found = []
        for pattern in self.SPECULATION_PATTERNS:
            matches = re.findall(pattern, response, re.IGNORECASE)
            speculation_found.extend(matches)
        
        if speculation_found:
            issues.append(f"Speculative language detected: {speculation_found[:3]}")
        
        is_supported = len(unsupported_claims) == 0 and len(speculation_found) == 0
        
        # Calculate confidence
        confidence = self._calculate_confidence(
            is_relevant, is_supported, is_grounded,
            term_overlap, grounding_term_count,
            len(unsupported_claims), len(speculation_found)
        )
        
        # Overall pass requires all checks
        overall_pass = is_relevant and is_supported and is_grounded and confidence >= 0.6
        
        if not overall_pass:
            logger.warning(f"Self-critique failed: {issues}")
        else:
            logger.info(f"Self-critique passed with confidence {confidence:.2f}")
        
        return CritiqueResult(
            is_supported=is_supported,
            is_relevant=is_relevant,
            is_grounded=is_grounded,
            overall_pass=overall_pass,
            issues=issues,
            unsupported_claims=unsupported_claims,
            confidence=confidence
        )

    def _calculate_confidence(
        self,
        is_relevant: bool,
        is_supported: bool,
        is_grounded: bool,
        term_overlap: float,
        grounding_count: int,
        unsupported_count: int,
        speculation_count: int
    ) -> float:
        """Calculate confidence score based on critique factors."""
        score = 0.0
        
        # Relevance contributes 0.3
        if is_relevant:
            score += 0.2 + (term_overlap * 0.1)
        
        # Grounding contributes 0.3
        if is_grounded:
            score += 0.2 + min(grounding_count / 5, 0.1)
        
        # Support contributes 0.4
        if is_supported:
            score += 0.4
        else:
            # Penalize based on unsupported claims
            penalty = min((unsupported_count + speculation_count) * 0.1, 0.4)
            score += max(0.4 - penalty, 0)
        
        return min(score, 1.0)

    def get_stricter_prompt_suffix(self, critique: CritiqueResult) -> str:
        """
        Generate a stricter prompt suffix based on critique issues.
        
        Returns additional instructions to add to the prompt for regeneration.
        """
        instructions = []
        
        if not critique.is_grounded:
            instructions.append(
                "IMPORTANT: You MUST cite specific policy references (Ref #XXX) for every claim."
            )
        
        if not critique.is_supported:
            instructions.append(
                "IMPORTANT: Do NOT use generalizations like 'typically', 'usually', or 'generally'. "
                "Only state what is explicitly in the policy documents."
            )
        
        if critique.unsupported_claims:
            instructions.append(
                f"AVOID these unsupported phrases: {', '.join(critique.unsupported_claims[:3])}"
            )
        
        if not critique.is_relevant:
            instructions.append(
                "IMPORTANT: Focus specifically on answering the user's question. "
                "Do not provide tangential information."
            )
        
        if instructions:
            return "\n\n" + "\n".join(instructions)
        return ""

    def should_regenerate(self, critique: CritiqueResult) -> bool:
        """
        Determine if response should be regenerated based on critique.
        
        For healthcare, we have a low tolerance for issues.
        """
        # Always regenerate if not grounded
        if not critique.is_grounded:
            return True
        
        # Regenerate if too many unsupported claims
        if len(critique.unsupported_claims) >= 2:
            return True
        
        # Regenerate if confidence is too low
        if critique.confidence < 0.5:
            return True
        
        # Don't regenerate if overall pass
        if critique.overall_pass:
            return False
        
        # Default: regenerate if any major issues
        return len(critique.issues) >= 2


# Singleton instance
_self_reflective_service: Optional[SelfReflectiveRAGService] = None


def get_self_reflective_service() -> SelfReflectiveRAGService:
    """Get or create the Self-Reflective RAG service singleton."""
    global _self_reflective_service
    if _self_reflective_service is None:
        _self_reflective_service = SelfReflectiveRAGService()
    return _self_reflective_service
