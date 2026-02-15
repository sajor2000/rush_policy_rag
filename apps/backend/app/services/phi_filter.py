"""
PHI (Protected Health Information) Redaction Filter.

Regex-based redaction of common PHI patterns from text before audit logging.
Covers the most common HIPAA Safe Harbor identifiers (45 CFR 164.514(b))
that may appear in user queries to a policy retrieval system.

This is a best-effort filter for the most common patterns. It is NOT a
substitute for a full NLP-based PII detection service for clinical data.
"""

import re
from typing import List, Tuple

# Compiled patterns for performance (evaluated once at module load).
# Each tuple: (compiled_regex, replacement_label)
_PHI_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # SSN (must precede generic digit patterns)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    # MRN / Medical Record Number (MRN: 1234567, MRN#1234567, MRN 1234567)
    (re.compile(r"\bMRN[:\s#]*\d{6,10}\b", re.IGNORECASE), "[MRN]"),
    # Date of birth patterns (MM/DD/YYYY, MM-DD-YYYY)
    (re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{4}\b"), "[DOB]"),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    # Phone numbers (US formats: 123-456-7890, (123) 456-7890, 123.456.7890)
    (
        re.compile(r"\b(?:\(\d{3}\)\s*|\d{3}[\-.])\d{3}[\-.]\d{4}\b"),
        "[PHONE]",
    ),
    # Two-word proper names ("John Smith") — placed last to avoid
    # false-matching policy titles that are handled separately.
    # Requires two consecutive capitalised words not at sentence start.
    (re.compile(r"(?<!\. )(?<!\A)\b[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}\b"), "[NAME]"),
]

# Known medical brand names and policy terms that look like personal names
# but should NOT be redacted.
_FALSE_POSITIVE_NAMES = frozenset(
    {
        "Smith Nephew",  # Smith & Nephew medical devices
        "Baxter International",
        "Rush University",
        "Rush Medical",
        "Rush Copley",
        "Rush Oak",
        "Saint Luke",
    }
)


def redact_phi(text: str) -> Tuple[str, List[str]]:
    """
    Redact common PHI patterns from text.

    Args:
        text: Raw text potentially containing PHI.

    Returns:
        Tuple of (redacted_text, flags) where flags lists what was found,
        e.g. ["[NAME]: 1", "[MRN]: 1"].
    """
    if not text:
        return text, []

    redacted = text
    flags: List[str] = []

    for pattern, label in _PHI_PATTERNS:
        matches = pattern.findall(redacted)
        # Filter out known false positives for name patterns
        if label == "[NAME]":
            matches = [m for m in matches if m not in _FALSE_POSITIVE_NAMES]
        if matches:
            flags.append(f"{label}: {len(matches)}")
            # Replace only genuine matches (skip false positives for names)
            if label == "[NAME]":
                for match in matches:
                    redacted = redacted.replace(match, label)
            else:
                redacted = pattern.sub(label, redacted)

    return redacted, flags
