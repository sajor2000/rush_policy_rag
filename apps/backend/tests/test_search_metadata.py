"""
Unit tests for search endpoint metadata completeness.

Verifies that the /api/search endpoint returns all metadata fields including
page_number, category, subcategory, etc. to match the /api/chat endpoint.
"""

import pytest
from app.services.search_result import SearchResult, search_result_to_item


def test_search_result_to_item_includes_page_number():
    """Verify page_number field is preserved in conversion."""
    result = SearchResult(
        content="Central line placement requires sterile technique.",
        title="Central Line Placement",
        page_number=5,
        reference_number="528"
    )
    item = search_result_to_item(result)

    assert item["page_number"] == 5
    assert item["reference_number"] == "528"


def test_search_result_to_item_includes_policy_number():
    """Verify canonical policy_number is preserved in conversion."""
    result = SearchResult(
        content="Shift differentials are paid for weekends and holidays.",
        title="Shift Differentials",
        reference_number="1209",
        policy_number="HR-C 05.00",
    )
    item = search_result_to_item(result)

    assert item["policy_number"] == "HR-C 05.00"
    assert item["reference_number"] == "1209"


def test_search_result_to_item_includes_enhanced_metadata():
    """Verify category, subcategory, and regulatory fields are preserved."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        category="Clinical",
        subcategory="Patient Safety",
        regulatory_citations="Joint Commission Standard PC.01.02.03",
        related_policies="529, 530"
    )
    item = search_result_to_item(result)

    assert item["category"] == "Clinical"
    assert item["subcategory"] == "Patient Safety"
    assert item["regulatory_citations"] == "Joint Commission Standard PC.01.02.03"
    assert item["related_policies"] == "529, 530"


def test_search_result_to_item_includes_hierarchical_fields():
    """Verify hierarchical chunking fields are preserved."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        chunk_level="section",
        parent_chunk_id="policy-528-doc",
        chunk_index=3
    )
    item = search_result_to_item(result)

    assert item["chunk_level"] == "section"
    assert item["parent_chunk_id"] == "policy-528-doc"
    assert item["chunk_index"] == 3


def test_search_result_to_item_includes_entity_booleans():
    """Verify all entity boolean flags are preserved."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        applies_to_rumc=True,
        applies_to_rumg=True,
        applies_to_rmg=False,
        applies_to_roph=False,
        applies_to_rcmc=True
    )
    item = search_result_to_item(result)

    assert item["applies_to_rumc"] is True
    assert item["applies_to_rumg"] is True
    assert item["applies_to_rmg"] is False
    assert item["applies_to_roph"] is False
    assert item["applies_to_rcmc"] is True


def test_search_result_to_item_includes_all_scoring_fields():
    """Verify both search score and reranker score are preserved."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        score=0.85,
        reranker_score=0.92
    )
    item = search_result_to_item(result)

    assert item["score"] == 0.85
    assert item["reranker_score"] == 0.92


def test_search_result_to_item_includes_source_tracking():
    """Verify source file, owner, and date fields are preserved."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        source_file="catheter_policy_528.pdf",
        document_owner="Clinical Services",
        date_updated="01/15/2024",
        date_approved="01/10/2024"
    )
    item = search_result_to_item(result)

    assert item["source_file"] == "catheter_policy_528.pdf"
    assert item["document_owner"] == "Clinical Services"
    assert item["date_updated"] == "01/15/2024"
    assert item["date_approved"] == "01/10/2024"


def test_search_result_to_item_handles_none_values():
    """Verify conversion handles None values gracefully."""
    result = SearchResult(
        content="Test content",
        title="Test Policy",
        page_number=None,
        category=None,
        subcategory=None,
        reranker_score=None
    )
    item = search_result_to_item(result)

    assert item["page_number"] is None
    assert item["category"] is None
    assert item["subcategory"] is None
    assert item["reranker_score"] is None


def test_search_result_to_item_complete_real_world_example():
    """Test with complete realistic data from a policy chunk."""
    result = SearchResult(
        content="Central line placement requires sterile technique...",
        citation="Central Line Placement (Ref 528, Section IV)",
        title="Central Line Placement and Maintenance",
        section="IV. PROCEDURE",
        reference_number="528",
        applies_to="RUMC, RUMG",
        date_updated="01/15/2024",
        date_approved="01/10/2024",
        document_owner="Clinical Services",
        source_file="catheter_policy_528.pdf",
        page_number=3,
        category="Clinical",
        subcategory="Patient Safety",
        regulatory_citations="Joint Commission",
        chunk_level="semantic",
        chunk_index=5,
        applies_to_rumc=True,
        applies_to_rumg=True,
        score=0.89,
        reranker_score=0.94
    )
    item = search_result_to_item(result)

    # Verify all critical fields
    assert item["page_number"] == 3
    assert item["reference_number"] == "528"
    assert item["source_file"] == "catheter_policy_528.pdf"
    assert item["category"] == "Clinical"
    assert item["content"] == "Central line placement requires sterile technique..."
    assert item["title"] == "Central Line Placement and Maintenance"
    assert item["applies_to_rumc"] is True
    assert item["reranker_score"] == 0.94


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
