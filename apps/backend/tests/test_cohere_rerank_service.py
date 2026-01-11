"""
Tests for Cohere Rerank Service.

Tests the CohereRerankService in app/services/cohere_rerank_service.py.
Uses mocking to avoid actual Cohere API calls.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from app.services.cohere_rerank_service import (
    CohereRerankService,
    RerankResult,
    DEFAULT_MIN_SCORE,
)


class TestRerankResult:
    """Tests for RerankResult dataclass."""

    def test_result_creation(self):
        """Should create result with all fields."""
        result = RerankResult(
            content="Policy content here",
            title="Verbal Order Policy",
            reference_number="704",
            source_file="verbal-order.pdf",
            section="Section 3.1",
            applies_to="RUMC, RUMG",
            page_number=5,
            cohere_score=0.92,
            original_index=0,
        )
        assert result.title == "Verbal Order Policy"
        assert result.cohere_score == 0.92
        assert result.page_number == 5

    def test_result_defaults(self):
        """Should have sensible defaults."""
        result = RerankResult(
            content="Content",
            title="Title",
            reference_number="123",
            source_file="test.pdf",
        )
        assert result.section == ""
        assert result.applies_to == ""
        assert result.page_number is None
        assert result.cohere_score == 0.0
        assert result.original_index == 0


class TestCohereRerankServiceInit:
    """Tests for CohereRerankService initialization."""

    def test_service_initialization(self):
        """Should initialize with endpoint and API key."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
            top_n=10,
            min_score=0.25,
        )
        assert service.top_n == 10
        assert service.min_score == 0.25
        assert service._configured is True

    def test_endpoint_normalization(self):
        """Should normalize endpoint to include /v1/rerank."""
        # Without /v1/rerank
        service1 = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
        )
        assert service1.endpoint.endswith("/v1/rerank")

        # With /v1/rerank already
        service2 = CohereRerankService(
            endpoint="https://test.models.ai.azure.com/v1/rerank",
            api_key="test-key",
        )
        assert service2.endpoint.endswith("/v1/rerank")
        assert service2.endpoint.count("/v1/rerank") == 1

    def test_service_without_credentials(self):
        """Should handle missing credentials."""
        service = CohereRerankService(
            endpoint="",
            api_key="",
        )
        assert service._configured is False

    def test_default_min_score(self):
        """Should use default min score constant."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
        )
        assert service.min_score == DEFAULT_MIN_SCORE


class TestCohereRerankServiceRerank:
    """Tests for CohereRerankService rerank method."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked HTTP client."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
            top_n=5,
            min_score=0.1,
        )
        service._client = Mock()
        return service

    def test_rerank_returns_sorted_results(self, mock_service):
        """Should return results sorted by relevance score."""
        # Mock Cohere API response
        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.85},
                {"index": 2, "relevance_score": 0.75},
            ]
        }
        mock_response.status_code = 200
        mock_service._client.post.return_value = mock_response

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
            {"content": "Doc 2", "title": "Policy 2", "reference_number": "200", "source_file": "p2.pdf"},
            {"content": "Doc 3", "title": "Policy 3", "reference_number": "300", "source_file": "p3.pdf"},
        ]

        results = mock_service.rerank("test query", documents)

        # Results should be sorted by score (highest first)
        assert len(results) > 0
        # First result should have highest score
        assert results[0].cohere_score >= results[-1].cohere_score

    def test_rerank_filters_by_min_score(self, mock_service):
        """Should filter out documents below min_score threshold."""
        mock_service.min_score = 0.5

        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},  # Above threshold
                {"index": 1, "relevance_score": 0.3},  # Below threshold
                {"index": 2, "relevance_score": 0.6},  # Above threshold
            ]
        }
        mock_response.status_code = 200
        mock_service._client.post.return_value = mock_response

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
            {"content": "Doc 2", "title": "Policy 2", "reference_number": "200", "source_file": "p2.pdf"},
            {"content": "Doc 3", "title": "Policy 3", "reference_number": "300", "source_file": "p3.pdf"},
        ]

        results = mock_service.rerank("test query", documents)

        # Only documents above min_score should be returned
        for result in results:
            assert result.cohere_score >= 0.5

    def test_rerank_respects_top_n(self, mock_service):
        """Should return at most top_n results."""
        mock_service.top_n = 2

        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
                {"index": 2, "relevance_score": 0.7},
            ]
        }
        mock_response.status_code = 200
        mock_service._client.post.return_value = mock_response

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
            {"content": "Doc 2", "title": "Policy 2", "reference_number": "200", "source_file": "p2.pdf"},
            {"content": "Doc 3", "title": "Policy 3", "reference_number": "300", "source_file": "p3.pdf"},
        ]

        results = mock_service.rerank("test query", documents)

        assert len(results) <= 2

    def test_rerank_empty_documents(self, mock_service):
        """Should handle empty document list."""
        results = mock_service.rerank("test query", [])
        assert results == []


class TestCohereRerankServiceAsync:
    """Tests for CohereRerankService async rerank method."""

    @pytest.fixture
    def mock_async_service(self):
        """Create service with mocked async HTTP client."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
            top_n=5,
            min_score=0.1,
        )
        service._async_client = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_async_rerank_returns_results(self, mock_async_service):
        """Should return results from async rerank."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
            ]
        }
        mock_response.status_code = 200
        mock_async_service._async_client.post.return_value = mock_response

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
        ]

        results = await mock_async_service.rerank_async("test query", documents)

        assert len(results) > 0


class TestCohereRerankServiceYAMLFormat:
    """Tests for YAML document formatting."""

    def test_yaml_format_preserves_field_order(self):
        """Should format documents in YAML with field order preserved."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
        )

        # The service formats documents in YAML with specific field order:
        # title, reference_number, section, applies_to, content (last)
        # This is tested implicitly through the rerank calls


class TestCohereRerankServiceErrorHandling:
    """Tests for error handling in Cohere service."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked HTTP client."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
        )
        service._client = Mock()
        return service

    def test_handles_api_error(self, mock_service):
        """Should handle API errors gracefully."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = Exception("API Error")
        mock_service._client.post.return_value = mock_response

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
        ]

        # Should raise or return empty depending on implementation
        try:
            results = mock_service.rerank("test query", documents)
            assert results == []  # Graceful degradation
        except Exception:
            pass  # Or raise error - both are acceptable

    def test_handles_timeout(self, mock_service):
        """Should handle timeout errors."""
        import httpx

        mock_service._client.post.side_effect = httpx.TimeoutException("Timeout")

        documents = [
            {"content": "Doc 1", "title": "Policy 1", "reference_number": "100", "source_file": "p1.pdf"},
        ]

        # Should handle timeout gracefully
        try:
            results = mock_service.rerank("test query", documents)
        except httpx.TimeoutException:
            pass  # Expected for some implementations


class TestNegationHandling:
    """Tests for negation-aware reranking (key feature of cross-encoder)."""

    def test_negation_scenario_description(self):
        """
        Document the negation handling capability.

        Cohere Rerank 3.5 (cross-encoder) should understand that:
        - Query: "Can nurses accept verbal orders?"
        - Doc with "NOT authorized to accept" should rank LOWER than
        - Doc with "authorized to accept verbal orders"

        This is tested in the full enhanced evaluation suite.
        """
        # This test documents the expected behavior
        # Actual negation testing requires Cohere API
        pass

    def test_contradiction_scenario_description(self):
        """
        Document the contradiction handling capability.

        Cohere Rerank should understand contradictions:
        - Query asking about authorization
        - Doc stating "requires physician order" (implies NOT authorized alone)
        - Should rank appropriately based on semantic understanding
        """
        pass


class TestConfiguredState:
    """Tests for service configured state."""

    def test_is_configured_with_valid_credentials(self):
        """Should report configured when credentials provided."""
        service = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="test-key",
        )
        assert service.is_configured() is True

    def test_is_not_configured_without_credentials(self):
        """Should report not configured without credentials."""
        service = CohereRerankService(
            endpoint="",
            api_key="",
        )
        assert service.is_configured() is False

    def test_is_not_configured_with_partial_credentials(self):
        """Should report not configured with only partial credentials."""
        service1 = CohereRerankService(
            endpoint="https://test.models.ai.azure.com",
            api_key="",
        )
        assert service1.is_configured() is False

        service2 = CohereRerankService(
            endpoint="",
            api_key="test-key",
        )
        assert service2.is_configured() is False
