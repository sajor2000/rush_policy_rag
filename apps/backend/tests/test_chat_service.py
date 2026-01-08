import asyncio

from app.models.schemas import ChatRequest
from app.services.chat_service import ChatService
from app.services.on_your_data_service import OnYourDataResult, OnYourDataReference


class DummySearchIndex:
    index_name = "dummy-index"

    def search(self, *args, **kwargs):  # pragma: no cover - not used in this test
        return []


class DummyOnYourDataService:
    def __init__(self):
        self.is_configured = True
        self.invocations = 0

    async def chat(self, **kwargs):
        self.invocations += 1
        return OnYourDataResult(
            answer="📋 QUICK ANSWER\nUse aseptic technique.\n\n📄 POLICY REFERENCE\nPolicy: Central Line Care\nReference Number: CL-101",
            citations=[
                OnYourDataReference(
                    content="Always use chlorhexidine.",
                    title="Central Line Care",
                    filepath="central-line-care.pdf",
                    reranker_score=3.8,
                )
            ],
            intent="infection-control",
            raw_response={"mock": True},
        )


def test_chat_service_uses_on_your_data_when_available():
    search_index = DummySearchIndex()
    on_your_data = DummyOnYourDataService()

    service = ChatService(
        search_index=search_index,
        on_your_data_service=on_your_data,
    )

    response = asyncio.run(service.process_chat(ChatRequest(message="How do we clean central lines?")))

    assert response.found is True
    assert response.summary.startswith("Use aseptic technique")
    assert response.evidence
    assert response.evidence[0].title == "Central Line Care"
    assert on_your_data.invocations == 1


# ============================================================================
# Device Ambiguity Detection Tests
# ============================================================================

def test_ambiguous_device_detection_iv():
    """Test that ambiguous 'IV' queries trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # AMBIGUOUS: "IV" without modifiers + device context
    ambiguous_queries = [
        "how long can an IV stay in place",
        "IV dwell time",
        "when should an IV be changed",
        "IV care procedure",
        "IV insertion requirements"
    ]

    for query in ambiguous_queries:
        result = service.detect_device_ambiguity(query)
        assert result is not None, f"Failed to detect ambiguity in: {query}"
        assert 'options' in result, f"Missing options in result for: {query}"
        assert len(result['options']) >= 3, f"Insufficient options for: {query}"
        assert result['ambiguous_term'] == 'iv', f"Wrong term detected for: {query}"
        assert 'message' in result


def test_clear_device_queries_no_clarification():
    """Test that clear device queries do NOT trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # CLEAR: Contains disambiguating modifiers
    clear_queries = [
        "peripheral IV insertion procedure",
        "PICC line dwell time",
        "central line dressing change",
        "urinary catheter removal",
        "Foley catheter care",
        "epidural catheter placement"
    ]

    for query in clear_queries:
        result = service.detect_device_ambiguity(query)
        assert result is None, f"False positive on clear query: {query}"


def test_ambiguous_catheter_detection():
    """Test that ambiguous 'catheter' queries trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    ambiguous_queries = [
        "catheter care procedures",
        "how to remove a catheter",
        "catheter insertion technique",
        "catheter dressing change"
    ]

    for query in ambiguous_queries:
        result = service.detect_device_ambiguity(query)
        assert result is not None, f"Failed to detect ambiguity in: {query}"
        assert result['ambiguous_term'] == 'catheter'
        assert len(result['options']) >= 3


def test_ambiguous_line_detection():
    """Test that ambiguous 'line' queries trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    ambiguous_queries = [
        "line dressing change frequency",
        "how long can a line stay in place",
        "line care protocol"
    ]

    for query in ambiguous_queries:
        result = service.detect_device_ambiguity(query)
        assert result is not None, f"Failed to detect ambiguity in: {query}"
        assert result['ambiguous_term'] == 'line'


def test_ambiguous_port_detection():
    """Test that ambiguous 'port' queries trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    ambiguous_queries = [
        "port flushing protocol",
        "how to access a port",
        "port care instructions"
    ]

    for query in ambiguous_queries:
        result = service.detect_device_ambiguity(query)
        assert result is not None, f"Failed to detect ambiguity in: {query}"
        assert result['ambiguous_term'] == 'port'


def test_non_device_queries_no_clarification():
    """Test that non-device queries don't trigger clarification."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    non_device_queries = [
        "hand hygiene policy",
        "patient fall prevention",
        "medication administration",
        "code blue procedure",
        "What is SBAR?"
    ]

    for query in non_device_queries:
        result = service.detect_device_ambiguity(query)
        assert result is None, f"False positive on non-device query: {query}"


def test_clarification_options_structure():
    """Test that clarification options have correct structure."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    result = service.detect_device_ambiguity("how long can an IV stay in place")

    assert result is not None
    assert 'message' in result
    assert 'options' in result
    assert 'ambiguous_term' in result
    assert 'requires_clarification' in result

    # Check option structure
    for option in result['options']:
        assert 'label' in option, "Option missing 'label'"
        assert 'expansion' in option, "Option missing 'expansion'"
        assert 'type' in option, "Option missing 'type'"
        assert len(option['label']) > 0, "Option label is empty"
        assert len(option['expansion']) > 0, "Option expansion is empty"


# ============================================================================
# Score Windowing Tests
# ============================================================================

class MockRerankResult:
    """Mock RerankResult for testing."""
    def __init__(self, score: float, content: str, reference_number: str, title: str = "Test Policy"):
        self.cohere_score = score
        self.content = content
        self.reference_number = reference_number
        self.title = title
        self.source_file = f"{reference_number}.pdf"
        self.section = ""
        self.applies_to = ""
        self.page_number = None
        self.original_index = 0


def test_score_windowing_filters_noise():
    """Test that score windowing filters out low-relevance noise."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # Simulate reranked results with score gap
    # Top 2 are about peripheral IV (score ~0.85)
    # Bottom 2 are about PICC/epidural (score ~0.40) - NOISE
    mock_reranked = [
        MockRerankResult(score=0.85, content="Peripheral IV dwell time: 72-96h", reference_number="123"),
        MockRerankResult(score=0.82, content="PIV insertion procedure", reference_number="124"),
        MockRerankResult(score=0.45, content="PICC line dwell time: weeks", reference_number="456"),  # NOISE
        MockRerankResult(score=0.38, content="Epidural catheter care", reference_number="789"),      # NOISE
    ]

    filtered = service.filter_by_score_window(
        mock_reranked,
        query="how long can an IV stay in place",
        window_threshold=0.6  # Keep score >= 0.51 (0.85 * 0.6)
    )

    # Should keep top 2 (0.85, 0.82) and filter out bottom 2 (0.45, 0.38)
    assert len(filtered) == 2, f"Expected 2 results, got {len(filtered)}"
    assert all(r.cohere_score >= 0.51 for r in filtered), "Filtered results below threshold"
    assert "123" in [r.reference_number for r in filtered], "Top result missing"
    assert "124" in [r.reference_number for r in filtered], "Second result missing"
    assert "456" not in [r.reference_number for r in filtered], "PICC noise not filtered"
    assert "789" not in [r.reference_number for r in filtered], "Epidural noise not filtered"


def test_score_windowing_skips_low_confidence():
    """Test that score windowing skips filtering when top score is too low."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # Low confidence results (top score < 0.3)
    mock_reranked = [
        MockRerankResult(score=0.25, content="Low confidence result 1", reference_number="A"),
        MockRerankResult(score=0.22, content="Low confidence result 2", reference_number="B"),
        MockRerankResult(score=0.18, content="Low confidence result 3", reference_number="C"),
    ]

    filtered = service.filter_by_score_window(
        mock_reranked,
        query="vague query",
        window_threshold=0.6
    )

    # Should NOT filter when top score < 0.3
    assert len(filtered) == len(mock_reranked), "Should not filter low-confidence results"


def test_score_windowing_prevents_over_filtering():
    """Test that score windowing keeps at least 2 results."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # Scenario: only 1 result would pass threshold
    mock_reranked = [
        MockRerankResult(score=0.90, content="Top result", reference_number="A"),
        MockRerankResult(score=0.50, content="Second result", reference_number="B"),  # Would be filtered
        MockRerankResult(score=0.45, content="Third result", reference_number="C"),   # Would be filtered
    ]

    filtered = service.filter_by_score_window(
        mock_reranked,
        query="specific query",
        window_threshold=0.6  # Would filter down to just 1 result (0.90)
    )

    # Should keep at least 2 results to prevent over-filtering
    assert len(filtered) >= 2, f"Over-filtered to {len(filtered)} results, should keep at least 2"


def test_score_windowing_skips_few_results():
    """Test that score windowing skips when there are only 2 or fewer results."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # Only 2 results
    mock_reranked = [
        MockRerankResult(score=0.85, content="Result 1", reference_number="A"),
        MockRerankResult(score=0.40, content="Result 2", reference_number="B"),
    ]

    filtered = service.filter_by_score_window(
        mock_reranked,
        query="query",
        window_threshold=0.6
    )

    # Should NOT filter when <= 2 results
    assert len(filtered) == 2, "Should not filter when only 2 results"


def test_score_windowing_keeps_tight_cluster():
    """Test that score windowing keeps all results in a tight score cluster."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    # All results are within tight score range
    mock_reranked = [
        MockRerankResult(score=0.88, content="Result 1", reference_number="A"),
        MockRerankResult(score=0.86, content="Result 2", reference_number="B"),
        MockRerankResult(score=0.84, content="Result 3", reference_number="C"),
        MockRerankResult(score=0.82, content="Result 4", reference_number="D"),
    ]

    filtered = service.filter_by_score_window(
        mock_reranked,
        query="query",
        window_threshold=0.6  # 0.88 * 0.6 = 0.528, all results pass
    )

    # All results should be kept (all above 0.528)
    assert len(filtered) == 4, f"Should keep all 4 tightly-clustered results, got {len(filtered)}"


def test_score_windowing_with_different_thresholds():
    """Test score windowing with different threshold values."""
    search_index = DummySearchIndex()
    service = ChatService(search_index=search_index)

    mock_reranked = [
        MockRerankResult(score=1.0, content="Perfect match", reference_number="A"),
        MockRerankResult(score=0.8, content="Good match", reference_number="B"),
        MockRerankResult(score=0.6, content="Okay match", reference_number="C"),
        MockRerankResult(score=0.4, content="Poor match", reference_number="D"),
    ]

    # Strict threshold (0.8) - should keep 2 results
    filtered_strict = service.filter_by_score_window(
        mock_reranked,
        query="query",
        window_threshold=0.8  # Keep >= 0.8 (1.0 * 0.8)
    )
    assert len(filtered_strict) == 2, f"Strict threshold should keep 2, got {len(filtered_strict)}"

    # Moderate threshold (0.6) - should keep 3 results
    filtered_moderate = service.filter_by_score_window(
        mock_reranked,
        query="query",
        window_threshold=0.6  # Keep >= 0.6 (1.0 * 0.6)
    )
    assert len(filtered_moderate) == 3, f"Moderate threshold should keep 3, got {len(filtered_moderate)}"

    # Lenient threshold (0.4) - should keep all 4 results
    filtered_lenient = service.filter_by_score_window(
        mock_reranked,
        query="query",
        window_threshold=0.4  # Keep >= 0.4 (1.0 * 0.4)
    )
    assert len(filtered_lenient) == 4, f"Lenient threshold should keep 4, got {len(filtered_lenient)}"
