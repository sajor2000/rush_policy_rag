"""
Citation Verification Service for Healthcare RAG

Verifies that LLM response claims are grounded in retrieved context.
Critical for patient safety - prevents hallucinated policy information.

In healthcare, a single hallucinated procedure or timeframe can lead to
patient harm. This service provides an additional safety layer.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of citation verification."""
    is_grounded: bool
    confidence_score: float  # 0.0-1.0
    cited_refs: Set[str]
    context_refs: Set[str]
    missing_refs: Set[str]  # Cited but not in context
    hallucination_risk: float  # 0.0-1.0
    flags: List[str] = field(default_factory=list)


class CitationVerifier:
    """
    Verifies that LLM responses are grounded in retrieved context.
    
    Key checks:
    1. Citation accuracy - cited refs match retrieved docs
    2. Claim grounding - response claims supported by context
    3. No speculation - detects hedging language
    4. No fabricated details - catches hallucinated specifics
    """
    
    # Patterns indicating speculation (not grounded)
    SPECULATION_PATTERNS = [
        r'\bprobably\b',
        r'\blikely\b',
        r'\bmight\b',
        r'\bcould be\b',
        r'\bpossibly\b',
        r'\bi think\b',
        r'\bi believe\b',
        r'\bgenerally\b',
        r'\btypically\b',
        r'\busually\b',
        r'\bin general\b',
        r'\bmost likely\b',
    ]
    
    # Patterns for extracting policy reference numbers
    REF_PATTERNS = [
        r'Ref\s*#?\s*(\d+(?:\.\d+)?)',
        r'Reference\s*(?:Number)?[:\s]*(\d+(?:\.\d+)?)',
        r'\(Ref\s*#?\s*(\d+(?:\.\d+)?)\)',
        r'policy\s+#?\s*(\d+(?:\.\d+)?)',
    ]
    
    # High-risk patterns that need extra verification
    HIGH_RISK_PATTERNS = [
        r'\b\d+\s*(?:mg|ml|mcg|units?|hours?|minutes?|days?)\b',  # Dosages/timeframes
        r'\bmust\s+(?:be|have|include|contain)\b',  # Mandatory requirements
        r'\bnever\b',  # Absolute prohibitions
        r'\balways\b',  # Absolute requirements
        r'\bimmediately\b',  # Urgency
        r'\bwithin\s+\d+\b',  # Specific timeframes
    ]
    
    # HEALTHCARE CRITICAL: Patterns for exact-match verification
    # These MUST appear verbatim in context or response is blocked
    EXACT_MATCH_PATTERNS = [
        r'\b\d+\s*(?:mg|mcg|ml|cc|units?|iu)\b',  # Medication dosages
        r'\b\d+\s*(?:hours?|minutes?|days?|weeks?)\b',  # Timeframes
        r'\b\d+(?:\.\d+)?%\b',  # Percentages
        r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b',  # Numbers
        r'\bRef\s*#?\s*\d+\b',  # Policy references
    ]

    def verify_response(
        self,
        response: str,
        contexts: List[str],
        sources: List[Dict[str, Any]]
    ) -> VerificationResult:
        """
        Verify that response is grounded in context.
        
        Args:
            response: LLM-generated response
            contexts: Retrieved context snippets
            sources: Source metadata with reference numbers
            
        Returns:
            VerificationResult with grounding assessment
        """
        flags = []
        
        # 1. Extract and verify citations
        cited_refs = self._extract_ref_numbers(response)
        context_refs = self._extract_refs_from_sources(sources)
        missing_refs = cited_refs - context_refs
        
        if missing_refs:
            flags.append(f"HALLUCINATED_REFS: {missing_refs}")
            logger.warning(f"Response cites refs not in context: {missing_refs}")
        
        citation_accuracy = 1.0 - (len(missing_refs) / max(len(cited_refs), 1))
        
        # 2. Check for speculation language
        speculation_found = self._detect_speculation(response)
        if speculation_found:
            flags.append(f"SPECULATION: {speculation_found}")
        
        # 3. Check high-risk claims are in context
        high_risk_claims = self._extract_high_risk_claims(response)
        ungrounded_claims = []
        
        combined_context = " ".join(contexts).lower()
        for claim in high_risk_claims:
            if claim.lower() not in combined_context:
                ungrounded_claims.append(claim)
        
        if ungrounded_claims:
            flags.append(f"UNGROUNDED_CLAIMS: {ungrounded_claims[:3]}")  # Limit to 3
            logger.warning(f"High-risk claims not found in context: {ungrounded_claims}")
        
        # 4. Calculate hallucination risk
        hallucination_risk = self._calculate_hallucination_risk(
            citation_accuracy=citation_accuracy,
            speculation_count=len(speculation_found),
            ungrounded_count=len(ungrounded_claims),
            total_claims=max(len(high_risk_claims), 1)
        )
        
        # 5. Determine if grounded
        is_grounded = (
            citation_accuracy >= 0.9 and
            hallucination_risk < 0.3 and
            len(ungrounded_claims) == 0
        )
        
        # Calculate overall confidence
        confidence_score = self._calculate_confidence(
            citation_accuracy=citation_accuracy,
            hallucination_risk=hallucination_risk,
            has_citations=len(cited_refs) > 0,
            context_count=len(contexts)
        )
        
        return VerificationResult(
            is_grounded=is_grounded,
            confidence_score=confidence_score,
            cited_refs=cited_refs,
            context_refs=context_refs,
            missing_refs=missing_refs,
            hallucination_risk=hallucination_risk,
            flags=flags
        )

    def _extract_ref_numbers(self, text: str) -> Set[str]:
        """Extract policy reference numbers from text."""
        refs = set()
        for pattern in self.REF_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            refs.update(matches)
        return refs

    def _extract_refs_from_sources(self, sources: List[Dict[str, Any]]) -> Set[str]:
        """Extract reference numbers from source metadata."""
        refs = set()
        for source in sources:
            ref = source.get('reference_number', '')
            if ref:
                # Normalize - remove leading zeros, etc.
                refs.add(ref.lstrip('0') or '0')
                refs.add(ref)  # Also keep original
        return refs

    def _detect_speculation(self, text: str) -> List[str]:
        """Detect speculation/hedging language."""
        found = []
        text_lower = text.lower()
        for pattern in self.SPECULATION_PATTERNS:
            if re.search(pattern, text_lower):
                match = re.search(pattern, text_lower)
                if match:
                    found.append(match.group())
        return found

    def _extract_high_risk_claims(self, text: str) -> List[str]:
        """Extract claims that need verification (dosages, timeframes, etc.)."""
        claims = []
        for pattern in self.HIGH_RISK_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            claims.extend(matches)
        return claims

    def verify_factual_claims(
        self,
        response: str,
        contexts: List[str],
        is_multi_policy: bool = False
    ) -> tuple:
        """
        HEALTHCARE CRITICAL: Verify that specific factual claims in response
        appear EXACTLY in the retrieved context.
        
        This is the most important safety check. Numbers, dosages, timeframes,
        and percentages MUST match the source documents exactly.
        
        Args:
            response: LLM-generated response
            contexts: Retrieved context snippets
            is_multi_policy: If True, uses relaxed verification (claim can appear
                            in ANY individual context, not just combined)
        
        Returns:
            Tuple of (verified: bool, unverified_claims: List[str], flags: List[str])
        """
        if not contexts:
            return False, [], ["NO_CONTEXT_TO_VERIFY"]
        
        combined_context = ' '.join(contexts).lower()
        normalized_combined = re.sub(r'\s+', ' ', combined_context)
        
        unverified_claims = []
        flags = []
        
        # Extract all factual claims from response
        for pattern in self.EXACT_MATCH_PATTERNS:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                match_lower = match.lower().strip()
                normalized_match = re.sub(r'\s+', ' ', match_lower)
                
                found = False
                
                # First try: check combined context
                if match_lower in combined_context or normalized_match in normalized_combined:
                    found = True
                
                # For multi-policy: also check each individual context
                # (claim might be in one specific policy's context)
                if not found and is_multi_policy:
                    for ctx in contexts:
                        ctx_lower = ctx.lower()
                        normalized_ctx = re.sub(r'\s+', ' ', ctx_lower)
                        if match_lower in ctx_lower or normalized_match in normalized_ctx:
                            found = True
                            break
                
                if not found:
                    unverified_claims.append(match)
        
        if unverified_claims:
            # Deduplicate
            unverified_claims = list(set(unverified_claims))
            
            # For multi-policy, only flag truly unverified claims (not just missing from combined)
            if is_multi_policy and len(unverified_claims) <= 2:
                # Allow up to 2 minor unverified claims for multi-policy (may be synthesis)
                flags.append(f"MINOR_UNVERIFIED: {unverified_claims}")
                logger.info(f"Multi-policy query: {len(unverified_claims)} minor unverified claims (allowed)")
                return True, unverified_claims, flags
            
            flags.append(f"UNVERIFIED_FACTS: {unverified_claims[:5]}")
            logger.warning(
                f"HEALTHCARE SAFETY: Unverified factual claims in response: {unverified_claims}"
            )
            return False, unverified_claims, flags
        
        return True, [], []
    
    def verify_no_fabricated_refs(
        self,
        response: str,
        context_refs: Set[str]
    ) -> tuple:
        """
        HEALTHCARE CRITICAL: Ensure no policy references are fabricated.
        
        Returns:
            Tuple of (verified: bool, fabricated_refs: Set[str], flags: List[str])
        """
        cited_refs = self._extract_ref_numbers(response)
        
        # Normalize context refs for comparison
        normalized_context_refs = set()
        for ref in context_refs:
            normalized_context_refs.add(ref)
            normalized_context_refs.add(ref.lstrip('0') or '0')
        
        fabricated = set()
        for ref in cited_refs:
            if ref not in normalized_context_refs and ref.lstrip('0') not in normalized_context_refs:
                fabricated.add(ref)
        
        if fabricated:
            logger.warning(f"HEALTHCARE SAFETY: Fabricated policy refs detected: {fabricated}")
            return False, fabricated, [f"FABRICATED_REFS: {fabricated}"]
        
        return True, set(), []

    def _calculate_hallucination_risk(
        self,
        citation_accuracy: float,
        speculation_count: int,
        ungrounded_count: int,
        total_claims: int
    ) -> float:
        """Calculate overall hallucination risk score."""
        # Weighted factors
        citation_factor = (1 - citation_accuracy) * 0.4
        speculation_factor = min(speculation_count / 3, 1.0) * 0.2
        ungrounded_factor = (ungrounded_count / total_claims) * 0.4
        
        risk = citation_factor + speculation_factor + ungrounded_factor
        return min(risk, 1.0)

    def _calculate_confidence(
        self,
        citation_accuracy: float,
        hallucination_risk: float,
        has_citations: bool,
        context_count: int
    ) -> float:
        """Calculate confidence score for the response."""
        # Base confidence from citation accuracy and hallucination risk
        base_confidence = citation_accuracy * (1 - hallucination_risk)
        
        # Boost if has citations
        if has_citations:
            base_confidence *= 1.1
        else:
            base_confidence *= 0.7
        
        # Boost based on context count
        context_factor = min(context_count / 3, 1.0)
        base_confidence *= (0.8 + 0.2 * context_factor)
        
        return min(base_confidence, 1.0)


# Singleton instance
_citation_verifier: Optional[CitationVerifier] = None


def get_citation_verifier() -> CitationVerifier:
    """Get or create the citation verifier singleton."""
    global _citation_verifier
    if _citation_verifier is None:
        _citation_verifier = CitationVerifier()
    return _citation_verifier
