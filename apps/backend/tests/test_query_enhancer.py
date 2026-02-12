#!/usr/bin/env python3
"""
Unit tests for query_enhancer.py bug fixes.

Tests:
- Bug 3: normalize_query_punctuation strips trailing/leading commas, semicolons, etc.
- Bug 2: detect_policy_number detects RUSH coded policy numbers and avoids false positives.

Usage:
    python -m pytest tests/test_query_enhancer.py -v
    python tests/test_query_enhancer.py  # standalone
"""

import sys
from pathlib import Path

# Add parent directory to path so imports work standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.query_enhancer import (
    normalize_query_punctuation,
    normalize_location_context,
    detect_policy_number,
)


# ============================================================================
# Bug 3: Punctuation normalization
# ============================================================================

class TestNormalizeQueryPunctuation:
    """Tests for trailing/leading punctuation stripping."""

    def test_trailing_comma(self):
        assert normalize_query_punctuation("Shift Differentials,") == "Shift Differentials"

    def test_trailing_comma_with_space(self):
        """Regression: trailing space after comma must still be stripped."""
        assert normalize_query_punctuation("Shift Differentials, ") == "Shift Differentials"

    def test_leading_comma(self):
        assert normalize_query_punctuation(",Shift Differentials") == "Shift Differentials"

    def test_trailing_semicolon(self):
        assert normalize_query_punctuation("Shift Differentials;") == "Shift Differentials"

    def test_trailing_period(self):
        assert normalize_query_punctuation("Shift Differentials.") == "Shift Differentials"

    def test_trailing_colon(self):
        assert normalize_query_punctuation("Shift Differentials:") == "Shift Differentials"

    def test_mid_query_comma_preserved(self):
        """Commas in the middle of queries must NOT be stripped."""
        assert normalize_query_punctuation("Shift Differentials, Premium Pay") == "Shift Differentials, Premium Pay"

    def test_question_mark_preserved(self):
        """Question marks indicate query intent and must be kept."""
        assert normalize_query_punctuation("What is hand hygiene?") == "What is hand hygiene?"

    def test_possessive_removed(self):
        assert normalize_query_punctuation("RUMC's NICU policy") == "RUMC NICU policy"

    def test_smart_quotes_normalized(self):
        assert normalize_query_punctuation("\u201csmart quotes\u201d") == '"smart quotes"'

    def test_empty_string(self):
        assert normalize_query_punctuation("") == ""

    def test_only_punctuation(self):
        assert normalize_query_punctuation(",;:.") == ""

    def test_multiple_trailing(self):
        assert normalize_query_punctuation("test...") == "test"

    def test_whitespace_normalized(self):
        assert normalize_query_punctuation("  extra   spaces  ") == "extra spaces"


class TestNormalizeLocationContext:
    """Tests for location/punctuation normalization preserving policy IDs."""

    def test_policy_decimal_preserved(self):
        normalized, _ = normalize_location_context("What is HR-C 05.00?")
        assert normalized == "What is HR-C 05.00?"

    def test_policy_decimal_not_split_when_followed_by_text(self):
        normalized, _ = normalize_location_context("Summarize HR-C 05.00 policy")
        assert "05.00" in normalized
        assert "05. 00" not in normalized


# ============================================================================
# Bug 2: Policy number detection
# ============================================================================

class TestDetectPolicyNumber:
    """Tests for RUSH policy number detection and normalization."""

    # --- Should match ---

    def test_standard_format(self):
        result = detect_policy_number("HR-C 05.00")
        assert result is not None
        assert result[0] == "HR-C 05.00"
        assert "search.ismatch" in result[1]
        assert "HR-C 05.00" in result[1]

    def test_without_sub_number(self):
        result = detect_policy_number("HR-C 05")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_lowercase(self):
        result = detect_policy_number("hr-c 05.00")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_no_space_after_dash(self):
        result = detect_policy_number("HR-C05.00")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_embedded_in_query(self):
        result = detect_policy_number("What is 6.02 in HR-C 06.00?")
        assert result is not None
        assert result[0] == "HR-C 06.00"

    def test_hr_b_format(self):
        result = detect_policy_number("Tell me about HR-B 13.00")
        assert result is not None
        assert result[0] == "HR-B 13.00"

    def test_single_digit_number(self):
        result = detect_policy_number("HR-C 5.00")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_malformed_hr_code_normalized(self):
        result = detect_policy_number("What is HR-C 0.600 Time and Attendance Recording?")
        assert result is not None
        assert result[0] == "HR-C 06.00"

    # --- Should NOT match (false positive prevention) ---

    def test_no_match_plain_english(self):
        assert detect_policy_number("hand hygiene policy") is None

    def test_no_match_natural_language_do_i(self):
        """Without required dash, 'do I 2' must NOT match."""
        assert detect_policy_number("do I 2 things") is None

    def test_no_match_natural_language_is_a(self):
        assert detect_policy_number("is A 3 page doc") is None

    def test_no_match_numeric_only_ref(self):
        """Numeric-only refs like '528' are a different format."""
        assert detect_policy_number("policy 528") is None

    def test_no_match_no_digits(self):
        assert detect_policy_number("submit the form") is None

    def test_no_match_empty_string(self):
        assert detect_policy_number("") is None

    def test_no_match_at_b_2(self):
        """'at B 2' should not match — no dash."""
        assert detect_policy_number("at B 2 locations") is None

    def test_odata_filter_format(self):
        """Verify the OData filter uses search.ismatch on title field."""
        result = detect_policy_number("HR-B 14.00")
        assert result is not None
        normalized, odata = result
        assert "search.ismatch" in odata
        assert "'\"HR-B 14.00\"'" in odata
        assert "'title'" in odata
        # Verify no injection characters in normalized ref
        assert "'" not in normalized


# ============================================================================
# Run standalone
# ============================================================================

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
