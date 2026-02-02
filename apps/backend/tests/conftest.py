"""
Pytest configuration and fixtures for RUSH PolicyTech RAG tests.

Provides:
- Azure OpenAI client fixtures
- DeepEval metric fixtures
- Backend API client fixtures
- Test data fixtures
"""

import os
import json
import pytest
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

# ============================================================================
# DeepEval Timeout Configuration (MUST be set before importing deepeval)
# ============================================================================
# Increase timeout for DeepEval metrics to avoid timeouts during LLM evaluation.
# Default is 60 seconds, but Azure OpenAI can take longer for complex evaluations.
# Using gpt-4.1-mini makes this faster, but we still need generous timeouts.
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "240")  # 4 min/attempt
os.environ.setdefault("DEEPEVAL_TIMEOUT_SECONDS", "600")  # 10 minutes total
os.environ.setdefault("DEEPEVAL_MAX_RETRIES", "3")  # Retry up to 3 times

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)


# ============================================================================
# Configuration
# ============================================================================

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
TEST_DATASET_PATH = Path(__file__).parent.parent / "data" / "deepeval_test_dataset.json"


# ============================================================================
# Skip markers for conditional test execution
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "critical: mark test as critical (highest priority)")
    config.addinivalue_line("markers", "deepeval: mark test as requiring DeepEval")
    config.addinivalue_line("markers", "integration: mark test as integration test requiring backend")
    config.addinivalue_line("markers", "slow: mark test as slow running")


# ============================================================================
# DeepEval Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def azure_model():
    """
    Fixture providing Azure OpenAI model for DeepEval metrics.

    Uses gpt-4.1-mini for evaluation (faster, cheaper, avoids token limits).

    Returns:
        AzureOpenAIModel instance or None if not configured
    """
    try:
        from deepeval.models import AzureOpenAIModel

        endpoint = os.getenv("AOAI_ENDPOINT")
        api_key = os.getenv("AOAI_API_KEY")
        # Use gpt-4.1-mini for evaluation to avoid token limits and reduce cost
        deployment = os.getenv("AOAI_EVAL_DEPLOYMENT", os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1-mini"))

        if not endpoint or not api_key:
            logger.warning("Azure OpenAI credentials not configured")
            return None

        logger.info(f"Initializing DeepEval with Azure model: {deployment}")

        # DeepEval's AzureOpenAIModel uses base_url and requires model param
        return AzureOpenAIModel(
            model=deployment,
            deployment_name=deployment,
            base_url=endpoint,
            api_key=api_key,
            api_version="2024-08-01-preview"
        )
    except ImportError:
        logger.warning("DeepEval not installed")
        return None
    except Exception as e:
        logger.warning(f"Failed to initialize Azure model: {e}")
        return None


@pytest.fixture(scope="session")
def faithfulness_metric(azure_model):
    """
    Fixture providing FaithfulnessMetric with healthcare threshold.

    Args:
        azure_model: Azure OpenAI model fixture

    Returns:
        FaithfulnessMetric instance or pytest.skip if unavailable
    """
    if azure_model is None:
        pytest.skip("Azure model not configured")

    try:
        from deepeval.metrics import FaithfulnessMetric

        return FaithfulnessMetric(
            threshold=0.85,  # Healthcare-calibrated
            model=azure_model,
            include_reason=True
        )
    except ImportError:
        pytest.skip("DeepEval not installed")


@pytest.fixture(scope="session")
def answer_relevancy_metric(azure_model):
    """
    Fixture providing AnswerRelevancyMetric.

    Args:
        azure_model: Azure OpenAI model fixture

    Returns:
        AnswerRelevancyMetric instance or pytest.skip if unavailable
    """
    if azure_model is None:
        pytest.skip("Azure model not configured")

    try:
        from deepeval.metrics import AnswerRelevancyMetric

        return AnswerRelevancyMetric(
            threshold=0.70,
            model=azure_model,
            include_reason=True
        )
    except ImportError:
        pytest.skip("DeepEval not installed")


@pytest.fixture(scope="session")
def context_precision_metric(azure_model):
    """
    Fixture providing ContextualPrecisionMetric.

    Args:
        azure_model: Azure OpenAI model fixture

    Returns:
        ContextualPrecisionMetric instance or pytest.skip if unavailable
    """
    if azure_model is None:
        pytest.skip("Azure model not configured")

    try:
        from deepeval.metrics import ContextualPrecisionMetric

        return ContextualPrecisionMetric(
            threshold=0.75,
            model=azure_model,
            include_reason=True
        )
    except ImportError:
        pytest.skip("DeepEval not installed")


@pytest.fixture(scope="session")
def deepeval_metrics(azure_model):
    """
    Fixture providing DeepEvalMetrics wrapper class.

    Args:
        azure_model: Azure OpenAI model fixture

    Returns:
        DeepEvalMetrics instance or pytest.skip if unavailable
    """
    if azure_model is None:
        pytest.skip("Azure model not configured")

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.evaluation.metrics import DeepEvalMetrics

        return DeepEvalMetrics()
    except ImportError as e:
        pytest.skip(f"DeepEvalMetrics not available: {e}")


@pytest.fixture(scope="session")
def rag_diagnostics(azure_model):
    """
    Fixture providing RAGDiagnostics for claim-level analysis.

    Args:
        azure_model: Azure OpenAI model fixture

    Returns:
        RAGDiagnostics instance or pytest.skip if unavailable
    """
    if azure_model is None:
        pytest.skip("Azure model not configured")

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.evaluation.diagnostics import RAGDiagnostics

        return RAGDiagnostics()
    except ImportError as e:
        pytest.skip(f"RAGDiagnostics not available: {e}")


# ============================================================================
# API Client Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def http_client():
    """
    Fixture providing HTTP client for backend API calls.

    Returns:
        httpx.Client instance or pytest.skip if unavailable
    """
    try:
        import httpx
        client = httpx.Client(timeout=60.0, base_url=BACKEND_URL)
        yield client
        client.close()
    except ImportError:
        pytest.skip("httpx not installed")


@pytest.fixture(scope="session")
def async_http_client():
    """
    Fixture providing async HTTP client for backend API calls.

    Returns:
        httpx.AsyncClient instance or pytest.skip if unavailable
    """
    try:
        import httpx
        return httpx.AsyncClient(timeout=60.0, base_url=BACKEND_URL)
    except ImportError:
        pytest.skip("httpx not installed")


@pytest.fixture
def query_backend(http_client):
    """
    Fixture providing function to query backend API.

    Args:
        http_client: HTTP client fixture

    Returns:
        Function that queries the backend
    """
    def _query(query: str) -> Dict[str, Any]:
        try:
            response = http_client.post(
                "/api/chat",
                json={"message": query}
            )
            response.raise_for_status()
            data = response.json()
            return {
                "response": data.get("response", ""),
                "context": data.get("citations", []),
                "metadata": data.get("metadata", {}),
            }
        except Exception as e:
            logger.error(f"Backend query failed: {e}")
            return {"response": "", "context": [], "error": str(e)}

    return _query


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def test_dataset() -> List[Dict[str, Any]]:
    """
    Fixture providing test dataset from JSON file.

    Returns:
        List of test cases
    """
    if not TEST_DATASET_PATH.exists():
        logger.warning(f"Test dataset not found at {TEST_DATASET_PATH}")
        return []

    with open(TEST_DATASET_PATH, "r") as f:
        data = json.load(f)

    return data.get("test_cases", [])


@pytest.fixture
def retrieval_test_cases(test_dataset) -> List[Dict[str, Any]]:
    """Fixture providing retrieval accuracy test cases."""
    return [tc for tc in test_dataset if tc.get("category") == "retrieval_accuracy"]


@pytest.fixture
def negation_test_cases(test_dataset) -> List[Dict[str, Any]]:
    """Fixture providing negation handling test cases."""
    return [tc for tc in test_dataset if tc.get("category") == "negation_handling"]


@pytest.fixture
def hallucination_test_cases(test_dataset) -> List[Dict[str, Any]]:
    """Fixture providing hallucination prevention test cases."""
    return [tc for tc in test_dataset if tc.get("category") == "hallucination_prevention"]


@pytest.fixture
def safety_critical_test_cases(test_dataset) -> List[Dict[str, Any]]:
    """Fixture providing safety-critical test cases."""
    return [tc for tc in test_dataset if tc.get("category") == "safety_critical"]


@pytest.fixture
def risen_test_cases(test_dataset) -> List[Dict[str, Any]]:
    """Fixture providing RISEN compliance test cases."""
    return [tc for tc in test_dataset if tc.get("category") == "risen_compliance"]


# ============================================================================
# Sample Data Fixtures
# ============================================================================

@pytest.fixture
def sample_query():
    """Fixture providing a sample query for testing."""
    return "What is the code blue policy?"


@pytest.fixture
def sample_response():
    """Fixture providing a sample response for testing."""
    return """According to RUSH Policy RU-IC-001, Code Blue is activated when a patient
    experiences cardiac arrest. The Code Blue team must respond within 4 minutes.
    The team includes: attending physician, charge nurse, respiratory therapist,
    and pharmacy representative."""


@pytest.fixture
def sample_context():
    """Fixture providing sample context chunks for testing."""
    return [
        "Policy RU-IC-001: Code Blue Protocol. When a patient experiences cardiac arrest, "
        "staff must immediately call Code Blue by dialing the emergency extension.",
        "The Code Blue response team includes: attending physician, charge nurse, "
        "respiratory therapist, and pharmacy representative.",
        "Response time target: The Code Blue team should arrive at the patient's "
        "location within 4 minutes of activation.",
    ]


# ============================================================================
# DeepEval Test Case Factory
# ============================================================================

@pytest.fixture
def create_test_case():
    """
    Fixture providing factory function to create DeepEval test cases.

    Returns:
        Function that creates LLMTestCase instances
    """
    def _create(
        query: str,
        response: str,
        context: List[str],
        expected_output: Optional[str] = None
    ):
        try:
            from deepeval.test_case import LLMTestCase

            return LLMTestCase(
                input=query,
                actual_output=response,
                retrieval_context=context,
                expected_output=expected_output,
            )
        except ImportError:
            pytest.skip("DeepEval not installed")

    return _create


# ============================================================================
# Environment Check Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def check_azure_credentials():
    """Fixture that checks if Azure credentials are configured."""
    endpoint = os.getenv("AOAI_ENDPOINT")
    api_key = os.getenv("AOAI_API_KEY")

    if not endpoint or not api_key:
        pytest.skip("Azure OpenAI credentials not configured")

    return True


@pytest.fixture(scope="session")
def check_backend_available(http_client):
    """Fixture that checks if backend is available."""
    try:
        response = http_client.get("/health")
        if response.status_code != 200:
            pytest.skip("Backend not available")
    except Exception:
        pytest.skip("Backend not available")

    return True
