"""
Regression tests covering all 17 test cases from the bug tracker (2026-02-12).

Tests are organized by bug ID:
- BUG-001: Multi-page retrieval (P0-Critical)
- BUG-002: Policy number lookup (P1-High)
- BUG-003: Punctuation-sensitive matching (P2-Medium)

Unit tests validate query preprocessing, metadata extraction, and chunk content
prefix generation. Integration tests (verify_hr_retrieval_regressions.py) cover
the end-to-end pipeline against a running backend.

Usage:
    python -m pytest tests/test_bug_tracker_regressions.py -v
"""

import sys
from pathlib import Path

# Add parent directory to path so imports work standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.query_enhancer import (
    normalize_query_punctuation,
    detect_policy_number,
)
from preprocessing.metadata_extractor import (
    extract_policy_number,
    normalize_policy_number,
)
from preprocessing.policy_chunk import PolicyChunk


# ============================================================================
# BUG-001: Multi-Page Retrieval
# Validates that the ingestion pipeline produces chunks with page numbers
# and that content prefix includes section metadata for Page 2+ retrieval.
# ============================================================================

class TestBug001ChunkContentPrefix:
    """REC-007: Section header prefixes make chunks self-describing."""

    def _make_chunk(self, **overrides) -> PolicyChunk:
        defaults = dict(
            chunk_id="test_1",
            policy_title="Time and Attendance Recording; Editing and Approval",
            policy_number="HR-C 06.00",
            reference_number="",
            section_number="6.03",
            section_title="Supervisor Entry/Editing",
            text="The supervisor must review entries.",
            date_updated="2026-01-15",
            applies_to="RUMC",
            source_file="HR-C 06.00.pdf",
            char_count=34,
            page_number=2,
        )
        defaults.update(overrides)
        return PolicyChunk(**defaults)

    def test_prefix_includes_policy_number(self):
        chunk = self._make_chunk()
        prefix = chunk._build_content_prefix()
        assert "HR-C 06.00" in prefix

    def test_prefix_includes_title(self):
        chunk = self._make_chunk()
        prefix = chunk._build_content_prefix()
        assert "Time and Attendance" in prefix

    def test_prefix_includes_section(self):
        chunk = self._make_chunk()
        prefix = chunk._build_content_prefix()
        assert "Section 6.03" in prefix
        assert "Supervisor Entry/Editing" in prefix

    def test_azure_document_content_has_prefix(self):
        """to_azure_document content field should start with the prefix."""
        chunk = self._make_chunk()
        doc = chunk.to_azure_document()
        assert doc["content"].startswith("[HR-C 06.00")
        assert "The supervisor must review entries." in doc["content"]

    def test_prefix_no_duplicate_policy_number_in_title(self):
        """If title starts with policy number, don't repeat it."""
        chunk = self._make_chunk(
            policy_title="HR-C 06.00 Time and Attendance Recording",
        )
        prefix = chunk._build_content_prefix()
        # Should not contain "HR-C 06.00" twice
        assert prefix.count("HR-C 06.00") == 1

    def test_prefix_empty_when_no_metadata(self):
        chunk = self._make_chunk(
            policy_title="",
            policy_number="",
            section_number="",
            section_title="",
        )
        assert chunk._build_content_prefix() == ""

    def test_chunk_page_number_preserved(self):
        """Chunks must carry page_number metadata for PDF navigation."""
        chunk = self._make_chunk(page_number=3)
        doc = chunk.to_azure_document()
        assert doc["page_number"] == 3

    def test_chunk_page_number_none_allowed(self):
        chunk = self._make_chunk(page_number=None)
        doc = chunk.to_azure_document()
        assert doc["page_number"] is None


# ============================================================================
# BUG-002: Policy Number Lookup
# Validates detection, normalization, and OData filter generation.
# ============================================================================

class TestBug002PolicyNumberDetection:
    """All TC-002 test cases from the bug tracker."""

    # --- TC-002-A/B: Standard format ---
    def test_summarize_hr_c_05_00(self):
        """TC-002-A: 'Can you summarize HR-C 05.00?'"""
        result = detect_policy_number("Can you summarize HR-C 05.00?")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_what_is_hr_c_05_00(self):
        """TC-002-B: 'What is HR-C 05.00?'"""
        result = detect_policy_number("What is HR-C 05.00?")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    # --- TC-002-C: Different policy code ---
    def test_what_is_hr_c_06_00(self):
        """TC-002-C: 'What is HR-C 06.00?'"""
        result = detect_policy_number("What is HR-C 06.00?")
        assert result is not None
        assert result[0] == "HR-C 06.00"

    # --- TC-002-D: Malformed variant HR-C 0.600 ---
    def test_malformed_hr_c_0_600(self):
        """TC-002-D: 'What is HR-C 0.600 Time and Attendance Recording?'"""
        result = detect_policy_number(
            "What is HR-C 0.600 Time and Attendance Recording?"
        )
        assert result is not None
        assert result[0] == "HR-C 06.00"

    # --- TC-002-E/F: Full title queries should NOT produce policy number filter ---
    # (These are handled by semantic/keyword search, not policy number filter)

    # --- OData filter structure ---
    def test_odata_has_three_conditions(self):
        result = detect_policy_number("HR-E 03.00")
        assert result is not None
        _, odata = result
        assert "policy_number eq" in odata
        assert "reference_number eq" in odata
        assert "search.ismatch" in odata

    # --- Edge cases from V-002-E ---
    def test_partial_number_hr_c_05(self):
        """V-002-E partial: 'What is HR-C 05?'"""
        result = detect_policy_number("What is HR-C 05?")
        assert result is not None
        assert result[0] == "HR-C 05.00"

    def test_mid_sentence(self):
        """V-002-E mid-sentence: 'Tell me about HR-C 05.00 and night shifts'"""
        result = detect_policy_number("Tell me about HR-C 05.00 and night shifts")
        assert result is not None
        assert result[0] == "HR-C 05.00"


class TestBug002PolicyNumberNormalization:
    """Ingestion-side policy number extraction and normalization."""

    def test_from_filename(self):
        result = extract_policy_number(
            filename="HR-C 05.00 Shift Differentials, Weekend and Holiday Premium Pay (1209).pdf"
        )
        assert result == "HR-C 05.00"

    def test_from_filename_underscores(self):
        result = extract_policy_number(filename="HR-C_05.00_Shift_Differentials.pdf")
        assert result == "HR-C 05.00"

    def test_normalize_missing_zero(self):
        assert normalize_policy_number("HR-C 5.00") == "HR-C 05.00"

    def test_normalize_no_sub(self):
        assert normalize_policy_number("HR-C 05") == "HR-C 05.00"

    def test_normalize_malformed_0_600(self):
        assert normalize_policy_number("HR-C 0.600") == "HR-C 06.00"

    def test_normalize_lowercase(self):
        assert normalize_policy_number("hr-c 05.00") == "HR-C 05.00"

    def test_normalize_no_space(self):
        assert normalize_policy_number("HR-C05.00") == "HR-C 05.00"


# ============================================================================
# BUG-003: Punctuation-Sensitive Matching
# Validates that punctuation within queries is normalized so partial title
# searches work regardless of commas/semicolons.
# ============================================================================

class TestBug003PunctuationNormalization:
    """All TC-003 test cases from the bug tracker."""

    # --- TC-003-A: Partial title without comma ---
    def test_shift_differentials_no_comma(self):
        """TC-003-A: 'Can you summarize Shift Differentials?'
        Query should not contain a comma that could interfere with matching."""
        result = normalize_query_punctuation("Can you summarize Shift Differentials?")
        assert "Shift Differentials" in result
        assert result.endswith("?")

    # --- TC-003-B: Different phrasing without comma ---
    def test_policy_on_shift_differentials(self):
        """TC-003-B: 'What's the policy on Shift Differentials?'"""
        result = normalize_query_punctuation("What's the policy on Shift Differentials?")
        # Possessive removed: What's -> What
        assert "Shift Differentials" in result
        assert "," not in result  # No spurious commas

    # --- TC-003-C: Trailing comma query ---
    def test_trailing_comma_stripped(self):
        """TC-003-C: 'What's the policy on Shift Differentials,'"""
        result = normalize_query_punctuation("What's the policy on Shift Differentials,")
        assert result.endswith("Differentials")
        assert not result.endswith(",")

    # --- TC-003-D: End-of-title word matches fine (no punctuation issue) ---
    def test_holiday_premium_pay(self):
        """TC-003-D: 'What's the policy on Holiday Premium Pay?' — should pass regardless."""
        result = normalize_query_punctuation("What's the policy on Holiday Premium Pay?")
        assert "Holiday Premium Pay" in result

    # --- V-003 verification cases ---
    def test_v003a_strip_trailing(self):
        """V-003-A: 'Shift Differentials,' -> 'Shift Differentials'"""
        assert normalize_query_punctuation("Shift Differentials,") == "Shift Differentials"

    def test_v003b_preserves_policy_hyphens(self):
        """V-003-B: 'HR-C 05.00' unchanged."""
        assert normalize_query_punctuation("HR-C 05.00") == "HR-C 05.00"

    def test_semicolon_in_title_replaced(self):
        """Semicolons like in 'Recording; Editing' become spaces for better matching."""
        result = normalize_query_punctuation(
            "Tell me about Time and Attendance Recording; Editing and Approval"
        )
        assert ";" not in result
        assert "Recording Editing" in result

    def test_colon_in_section_replaced(self):
        """Colons in section references become spaces."""
        result = normalize_query_punctuation("Section 6.03: Supervisor Entry")
        assert ":" not in result
        assert "6.03" in result  # Period preserved

    def test_mid_query_comma_becomes_space(self):
        """Commas within query become spaces for better keyword tokenization."""
        result = normalize_query_punctuation(
            "Shift Differentials, Weekend and Holiday Premium Pay"
        )
        assert "," not in result
        assert "Shift Differentials Weekend" in result


# ============================================================================
# Run standalone
# ============================================================================

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
