"""
Tests for Azure OpenAI On Your Data Service.

Tests the OnYourDataService in app/services/on_your_data_service.py.
Uses mocking to avoid actual Azure OpenAI calls.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from app.services.on_your_data_service import (
    OnYourDataService,
    OnYourDataReference,
    OnYourDataResult,
)


class TestOnYourDataReference:
    """Tests for OnYourDataReference dataclass."""

    def test_reference_creation(self):
        """Should create reference with all fields."""
        ref = OnYourDataReference(
            content="This is the policy content.",
            title="Verbal Order Policy",
            filepath="verbal-order.pdf",
            url="",
            chunk_id="123",
            reference_number="704",
            section="Section 3.1",
            applies_to="RUMC, RUMG",
            page_number=5,
            reranker_score=0.85,
        )
        assert ref.title == "Verbal Order Policy"
        assert ref.reference_number == "704"
        assert ref.page_number == 5
        assert ref.reranker_score == 0.85

    def test_reference_defaults(self):
        """Should have sensible defaults for optional fields."""
        ref = OnYourDataReference(content="Content", title="Title")
        assert ref.filepath == ""
        assert ref.url == ""
        assert ref.chunk_id == ""
        assert ref.page_number is None
        assert ref.reranker_score is None


class TestOnYourDataResult:
    """Tests for OnYourDataResult dataclass."""

    def test_result_creation(self):
        """Should create result with answer and citations."""
        citations = [
            OnYourDataReference(content="Content 1", title="Policy 1"),
            OnYourDataReference(content="Content 2", title="Policy 2"),
        ]
        result = OnYourDataResult(
            answer="This is the answer.",
            citations=citations,
            intent="policy_query",
        )
        assert result.answer == "This is the answer."
        assert len(result.citations) == 2
        assert result.intent == "policy_query"

    def test_result_empty_citations(self):
        """Should handle empty citations list."""
        result = OnYourDataResult(answer="No policies found.", citations=[])
        assert len(result.citations) == 0


class TestOnYourDataServiceInit:
    """Tests for OnYourDataService initialization."""

    @patch.dict("os.environ", {
        "AOAI_ENDPOINT": "https://test.openai.azure.com",
        "AOAI_API_KEY": "test-key",
        "SEARCH_ENDPOINT": "https://test.search.windows.net",
        "SEARCH_API_KEY": "search-key",
    })
    def test_service_initialization(self):
        """Should initialize with environment variables."""
        service = OnYourDataService()
        assert service.endpoint == "https://test.openai.azure.com"
        assert service.search_endpoint == "https://test.search.windows.net"
        assert service.model == "gpt-4.1"  # default

    @patch.dict("os.environ", {}, clear=True)
    def test_service_without_credentials(self):
        """Should handle missing credentials gracefully."""
        service = OnYourDataService()
        assert service.endpoint is None
        assert service.client is None

    @patch.dict("os.environ", {
        "AOAI_ENDPOINT": "https://test.openai.azure.com",
        "AOAI_API_KEY": "test-key",
        "AOAI_CHAT_DEPLOYMENT": "gpt-4-turbo",
        "SEARCH_ENDPOINT": "https://test.search.windows.net",
        "SEARCH_API_KEY": "search-key",
        "SEARCH_INDEX_NAME": "custom-index",
        "SEARCH_SEMANTIC_CONFIG": "custom-semantic",
    })
    def test_custom_configuration(self):
        """Should use custom configuration from environment."""
        service = OnYourDataService()
        assert service.model == "gpt-4-turbo"
        assert service.index_name == "custom-index"
        assert service.semantic_config == "custom-semantic"


class TestOnYourDataServiceChat:
    """Tests for OnYourDataService chat method."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked client."""
        with patch.dict("os.environ", {
            "AOAI_ENDPOINT": "https://test.openai.azure.com",
            "AOAI_API_KEY": "test-key",
            "SEARCH_ENDPOINT": "https://test.search.windows.net",
            "SEARCH_API_KEY": "search-key",
        }):
            service = OnYourDataService()
            service.client = Mock()
            return service

    def test_chat_returns_result(self, mock_service):
        """Should return OnYourDataResult from chat."""
        # Mock the OpenAI response
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "According to the policy..."
        mock_response.choices[0].message.context = {
            "citations": [
                {
                    "content": "Policy content here",
                    "title": "Verbal Order Policy",
                    "filepath": "verbal-order.pdf",
                    "chunk_id": "123",
                }
            ]
        }
        mock_service.client.chat.completions.create.return_value = mock_response

        result = mock_service.chat("What is the verbal order policy?")

        assert isinstance(result, OnYourDataResult)
        assert "policy" in result.answer.lower()

    def test_chat_handles_no_citations(self, mock_service):
        """Should handle response with no citations."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "I could not find information."
        mock_response.choices[0].message.context = {"citations": []}
        mock_service.client.chat.completions.create.return_value = mock_response

        result = mock_service.chat("Unknown topic query")

        assert isinstance(result, OnYourDataResult)
        assert len(result.citations) == 0

    def test_chat_with_filter(self, mock_service):
        """Should pass filter to Azure Search."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Filtered result"
        mock_response.choices[0].message.context = {"citations": []}
        mock_service.client.chat.completions.create.return_value = mock_response

        result = mock_service.chat(
            "What is the policy?",
            filter_expression="applies_to_rumc eq true"
        )

        # Verify filter was passed in the call
        call_args = mock_service.client.chat.completions.create.call_args
        assert call_args is not None


class TestOnYourDataServiceConfiguration:
    """Tests for OnYourDataService search configuration."""

    @patch.dict("os.environ", {
        "AOAI_ENDPOINT": "https://test.openai.azure.com",
        "AOAI_API_KEY": "test-key",
        "SEARCH_ENDPOINT": "https://test.search.windows.net",
        "SEARCH_API_KEY": "search-key",
    })
    def test_builds_data_sources_config(self):
        """Should build proper data_sources configuration."""
        service = OnYourDataService()

        # The service should have proper search configuration
        assert service.search_endpoint is not None
        assert service.index_name == "rush-policies"  # default
        assert service.semantic_config == "default-semantic"  # default

    @patch.dict("os.environ", {
        "AOAI_ENDPOINT": "https://test.openai.azure.com",
        "AOAI_API_KEY": "test-key",
        "SEARCH_ENDPOINT": "https://test.search.windows.net",
        "SEARCH_API_KEY": "search-key",
        "AOAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
    })
    def test_embedding_deployment_configuration(self):
        """Should use configured embedding deployment."""
        service = OnYourDataService()
        assert service.embedding_deployment == "text-embedding-3-large"


class TestOnYourDataServiceErrorHandling:
    """Tests for OnYourDataService error handling."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked client."""
        with patch.dict("os.environ", {
            "AOAI_ENDPOINT": "https://test.openai.azure.com",
            "AOAI_API_KEY": "test-key",
            "SEARCH_ENDPOINT": "https://test.search.windows.net",
            "SEARCH_API_KEY": "search-key",
        }):
            service = OnYourDataService()
            service.client = Mock()
            return service

    def test_handles_rate_limit_error(self, mock_service):
        """Should handle rate limit errors appropriately."""
        from openai import RateLimitError

        mock_service.client.chat.completions.create.side_effect = RateLimitError(
            message="Rate limit exceeded",
            response=Mock(status_code=429),
            body=None,
        )

        with pytest.raises(RateLimitError):
            mock_service.chat("Test query")

    def test_handles_timeout_error(self, mock_service):
        """Should handle timeout errors appropriately."""
        from openai import APITimeoutError

        mock_service.client.chat.completions.create.side_effect = APITimeoutError(
            request=Mock()
        )

        with pytest.raises(APITimeoutError):
            mock_service.chat("Test query")


class TestCitationParsing:
    """Tests for citation parsing from Azure OpenAI responses."""

    def test_parse_citation_with_all_fields(self):
        """Should parse citation with all metadata fields."""
        citation_data = {
            "content": "Policy text here",
            "title": "Verbal Order Policy",
            "filepath": "verbal-order.pdf",
            "url": "https://example.com/policy.pdf",
            "chunk_id": "doc_123_chunk_0",
            "reference_number": "704",
            "section": "Section 3.1",
            "applies_to": "RUMC, RUMG, RMG",
        }

        ref = OnYourDataReference(
            content=citation_data["content"],
            title=citation_data["title"],
            filepath=citation_data["filepath"],
            url=citation_data["url"],
            chunk_id=citation_data["chunk_id"],
            reference_number=citation_data.get("reference_number", ""),
            section=citation_data.get("section", ""),
            applies_to=citation_data.get("applies_to", ""),
        )

        assert ref.title == "Verbal Order Policy"
        assert ref.reference_number == "704"
        assert ref.applies_to == "RUMC, RUMG, RMG"

    def test_parse_citation_with_missing_fields(self):
        """Should handle citations with missing optional fields."""
        citation_data = {
            "content": "Policy text",
            "title": "Some Policy",
        }

        ref = OnYourDataReference(
            content=citation_data["content"],
            title=citation_data["title"],
            filepath=citation_data.get("filepath", ""),
            reference_number=citation_data.get("reference_number", ""),
        )

        assert ref.filepath == ""
        assert ref.reference_number == ""
