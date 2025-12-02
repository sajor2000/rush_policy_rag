"""
Response Safety Validator for Healthcare RAG

Final safety checkpoint before returning responses to users.
In high-risk healthcare environments, this prevents:
- Hallucinated medication dosages
- Missing citations
- Speculative language
- Low-confidence responses being presented as authoritative

Patient safety depends on every response being accurate and grounded.
"""

import re
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SafetyCheckType(Enum):
    """Types of safety checks performed."""
    CITATION_PRESENT = "citation_present"
    NO_MEDICATION_HALLUCINATION = "no_medication_hallucination"
    NO_SPECULATION = "no_speculation"
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    NO_ABSOLUTE_CLAIMS = "no_absolute_claims"
    GROUNDED_RESPONSE = "grounded_response"


@dataclass
class SafetyCheck:
    """Result of a single safety check."""
    check_type: SafetyCheckType
    passed: bool
    message: str
    severity: str = "warning"  # "warning", "error", "critical"


@dataclass
class SafetyResult:
    """Overall safety validation result."""
    safe: bool
    checks: List[SafetyCheck]
    needs_human_review: bool
    confidence_level: str  # "high", "medium", "low"
    flags: List[str] = field(default_factory=list)
    fallback_response: Optional[str] = None


class ResponseSafetyValidator:
    """
    Final safety validation for healthcare RAG responses.
    
    Implements multiple safety checks to ensure responses are:
    1. Properly cited
    2. Free of hallucinated medical details
    3. Not speculative
    4. Above confidence threshold
    5. Appropriately grounded
    """
    
    # Medication/dosage patterns that should ONLY appear if in context
    MEDICATION_PATTERNS = [
        r'\b\d+\s*(?:mg|mcg|ml|cc|units?|iu)\b',
        r'\b(?:dose|dosage|dosing)\s*(?:of|:)?\s*\d+',
        r'\badminister\s+\d+',
        r'\bgive\s+\d+\s*(?:mg|ml|units?)',
    ]
    
    # Speculation patterns (not appropriate for policy responses)
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
    ]
    
    # Dangerous absolute claims without proper citation
    ABSOLUTE_CLAIM_PATTERNS = [
        r'\balways\s+(?:must|should|requires?)\b',
        r'\bnever\s+(?:should|allowed|permitted)\b',
        r'\bunder\s+no\s+circumstances\b',
        r'\babsolutely\s+(?:must|required|necessary)\b',
    ]
    
    # Citation patterns to verify presence
    CITATION_PATTERNS = [
        r'Ref\s*#?\s*\d+',
        r'Reference\s*(?:Number)?[:\s]*\d+',
        r'\(Ref\s*#?\s*\d+\)',
        r'policy\s+(?:title|name)?[:\s]*["\']?[\w\s]+["\']?\s*\(Ref',
    ]
    
    # Minimum confidence threshold for healthcare
    MIN_CONFIDENCE_THRESHOLD = 0.5
    
    # High confidence threshold
    HIGH_CONFIDENCE_THRESHOLD = 0.7

    def __init__(self, strict_mode: bool = True):
        """
        Initialize safety validator.
        
        Args:
            strict_mode: If True, enforce stricter checks for healthcare
        """
        self.strict_mode = strict_mode

    def validate(
        self,
        response_text: str,
        contexts: List[str],
        confidence_score: float,
        has_evidence: bool = True
    ) -> SafetyResult:
        """
        Perform comprehensive safety validation on a response.
        
        Args:
            response_text: The LLM-generated response
            contexts: Retrieved context snippets
            confidence_score: Confidence score from reranking (0.0-1.0)
            has_evidence: Whether evidence items were found
            
        Returns:
            SafetyResult with all check results
        """
        checks = []
        flags = []
        combined_context = " ".join(contexts).lower()
        
        # Check 1: Citation present
        citation_check = self._check_citations_present(response_text, has_evidence)
        checks.append(citation_check)
        if not citation_check.passed:
            flags.append("NO_CITATION")
        
        # Check 2: No medication hallucination
        med_check = self._check_no_medication_hallucination(response_text, combined_context)
        checks.append(med_check)
        if not med_check.passed:
            flags.append("MEDICATION_HALLUCINATION_RISK")
        
        # Check 3: No speculation
        spec_check = self._check_no_speculation(response_text)
        checks.append(spec_check)
        if not spec_check.passed:
            flags.append("SPECULATION_DETECTED")
        
        # Check 4: Confidence threshold
        conf_check = self._check_confidence_threshold(confidence_score)
        checks.append(conf_check)
        if not conf_check.passed:
            flags.append("LOW_CONFIDENCE")
        
        # Check 5: Absolute claims grounded
        abs_check = self._check_absolute_claims(response_text, combined_context)
        checks.append(abs_check)
        if not abs_check.passed:
            flags.append("UNGROUNDED_ABSOLUTE_CLAIM")
        
        # Determine overall safety
        critical_failures = [c for c in checks if not c.passed and c.severity == "critical"]
        error_failures = [c for c in checks if not c.passed and c.severity == "error"]
        warning_failures = [c for c in checks if not c.passed and c.severity == "warning"]
        
        # Safe if no critical or error failures
        safe = len(critical_failures) == 0 and len(error_failures) == 0
        
        # In strict mode, warnings also affect safety
        if self.strict_mode and len(warning_failures) > 1:
            safe = False
        
        # Determine confidence level
        if confidence_score >= self.HIGH_CONFIDENCE_THRESHOLD and len(flags) == 0:
            confidence_level = "high"
        elif confidence_score >= self.MIN_CONFIDENCE_THRESHOLD and len(error_failures) == 0:
            confidence_level = "medium"
        else:
            confidence_level = "low"
        
        # Determine if human review needed
        needs_human_review = (
            confidence_level == "low" or
            len(critical_failures) > 0 or
            "MEDICATION_HALLUCINATION_RISK" in flags
        )
        
        # Set fallback response if not safe
        fallback_response = None
        if not safe:
            from app.core.config import settings
            fallback_response = (
                f"I could not find reliable information in RUSH policies to answer this question. "
                f"Please verify at {settings.POLICYTECH_URL} or contact Policy Administration."
            )
        
        logger.info(
            f"Safety validation: safe={safe}, confidence={confidence_level}, "
            f"flags={flags}, human_review={needs_human_review}"
        )
        
        return SafetyResult(
            safe=safe,
            checks=checks,
            needs_human_review=needs_human_review,
            confidence_level=confidence_level,
            flags=flags,
            fallback_response=fallback_response
        )

    def _check_citations_present(self, response: str, has_evidence: bool) -> SafetyCheck:
        """Check that response includes proper citations."""
        has_citation = any(
            re.search(pattern, response, re.IGNORECASE)
            for pattern in self.CITATION_PATTERNS
        )
        
        # If we have evidence but no citation in response, that's a problem
        if has_evidence and not has_citation:
            return SafetyCheck(
                check_type=SafetyCheckType.CITATION_PRESENT,
                passed=False,
                message="Response has evidence but missing citation in text",
                severity="warning"
            )
        
        # If no evidence and no citation, might be "not found" response (OK)
        if not has_evidence and not has_citation:
            return SafetyCheck(
                check_type=SafetyCheckType.CITATION_PRESENT,
                passed=True,
                message="No evidence found (not found response)",
                severity="warning"
            )
        
        return SafetyCheck(
            check_type=SafetyCheckType.CITATION_PRESENT,
            passed=True,
            message="Citation present in response",
            severity="warning"
        )

    def _check_no_medication_hallucination(self, response: str, context: str) -> SafetyCheck:
        """Check that medication/dosage info in response exists in context."""
        response_lower = response.lower()
        
        for pattern in self.MEDICATION_PATTERNS:
            matches = re.findall(pattern, response_lower)
            for match in matches:
                # Check if this specific dosage appears in context
                if match not in context:
                    return SafetyCheck(
                        check_type=SafetyCheckType.NO_MEDICATION_HALLUCINATION,
                        passed=False,
                        message=f"Medication/dosage '{match}' not found in context",
                        severity="critical"  # Critical for patient safety
                    )
        
        return SafetyCheck(
            check_type=SafetyCheckType.NO_MEDICATION_HALLUCINATION,
            passed=True,
            message="No ungrounded medication information detected",
            severity="critical"
        )

    def _check_no_speculation(self, response: str) -> SafetyCheck:
        """Check that response doesn't contain speculative language."""
        response_lower = response.lower()
        found_speculation = []
        
        for pattern in self.SPECULATION_PATTERNS:
            if re.search(pattern, response_lower):
                match = re.search(pattern, response_lower)
                if match:
                    found_speculation.append(match.group())
        
        if found_speculation:
            return SafetyCheck(
                check_type=SafetyCheckType.NO_SPECULATION,
                passed=False,
                message=f"Speculative language detected: {found_speculation[:3]}",
                severity="warning"
            )
        
        return SafetyCheck(
            check_type=SafetyCheckType.NO_SPECULATION,
            passed=True,
            message="No speculative language detected",
            severity="warning"
        )

    def _check_confidence_threshold(self, confidence_score: float) -> SafetyCheck:
        """Check that confidence meets minimum threshold."""
        if confidence_score < self.MIN_CONFIDENCE_THRESHOLD:
            return SafetyCheck(
                check_type=SafetyCheckType.CONFIDENCE_THRESHOLD,
                passed=False,
                message=f"Confidence {confidence_score:.2f} below threshold {self.MIN_CONFIDENCE_THRESHOLD}",
                severity="error"
            )
        
        return SafetyCheck(
            check_type=SafetyCheckType.CONFIDENCE_THRESHOLD,
            passed=True,
            message=f"Confidence {confidence_score:.2f} meets threshold",
            severity="error"
        )

    def _check_absolute_claims(self, response: str, context: str) -> SafetyCheck:
        """Check that absolute claims (always, never) are grounded in context."""
        response_lower = response.lower()
        
        for pattern in self.ABSOLUTE_CLAIM_PATTERNS:
            matches = re.findall(pattern, response_lower)
            for match in matches:
                # These strong claims should appear in the source context
                if match not in context:
                    return SafetyCheck(
                        check_type=SafetyCheckType.NO_ABSOLUTE_CLAIMS,
                        passed=False,
                        message=f"Absolute claim '{match}' not grounded in context",
                        severity="error"
                    )
        
        return SafetyCheck(
            check_type=SafetyCheckType.NO_ABSOLUTE_CLAIMS,
            passed=True,
            message="Absolute claims are grounded in context",
            severity="error"
        )


# Singleton instance
_safety_validator: Optional[ResponseSafetyValidator] = None
_safety_validator_strict_mode: Optional[bool] = None


def get_safety_validator(strict_mode: bool = True) -> ResponseSafetyValidator:
    """
    Get or create the safety validator singleton.
    
    Note: The singleton is created with the strict_mode from the first call.
    Subsequent calls with a different strict_mode will log a warning.
    For healthcare, always use strict_mode=True.
    """
    global _safety_validator, _safety_validator_strict_mode
    
    if _safety_validator is None:
        _safety_validator = ResponseSafetyValidator(strict_mode=strict_mode)
        _safety_validator_strict_mode = strict_mode
        logger.info(f"SafetyValidator initialized with strict_mode={strict_mode}")
    elif _safety_validator_strict_mode != strict_mode:
        logger.warning(
            f"SafetyValidator requested with strict_mode={strict_mode} but "
            f"singleton was created with strict_mode={_safety_validator_strict_mode}. "
            f"Using existing instance."
        )
    
    return _safety_validator
