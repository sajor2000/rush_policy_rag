# Testing Guide

> Test strategy and commands for the RUSH Policy RAG system.
>
> Last Updated: 2026-01-11

## Quick Start

```bash
# Backend unit tests
cd apps/backend
pytest tests/ -v

# Frontend type check
cd apps/frontend
npm run check

# Full evaluation suite
cd apps/backend
python scripts/run_test_dataset.py
```

---

## Test Types

### 1. Backend Unit Tests

Location: `apps/backend/tests/`

```bash
cd apps/backend

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov-report=html

# Run specific test file
pytest tests/test_chat_service.py -v

# Run specific test
pytest tests/test_chat_service.py::test_process_chat -v
```

**Test Files:**

| File | Tests |
|------|-------|
| `test_auth.py` | Azure AD authentication |
| `test_chat_service.py` | Chat orchestration |
| `test_cohere_rerank_service.py` | Cohere reranking |
| `test_on_your_data_service.py` | Azure OpenAI integration |
| `test_query_validation.py` | Input validation |
| `test_security.py` | Security checks |
| `test_synonym_service.py` | Query expansion |
| `test_citation_formatter.py` | Citation formatting |
| `test_location_normalization.py` | Location parsing |

### 2. Frontend Checks

Location: `apps/frontend/`

```bash
cd apps/frontend

# TypeScript type checking
npm run check

# ESLint
npm run lint

# Build (catches additional errors)
npm run build
```

### 3. RAG Evaluation Suite

Location: `apps/backend/` (uses root scripts)

```bash
cd apps/backend

# Core test dataset (36 tests)
python scripts/run_test_dataset.py

# Enhanced evaluation (60+ tests)
python scripts/run_enhanced_evaluation.py

# Specific category tests
python scripts/run_enhanced_evaluation.py --category cohere_negation
python scripts/run_enhanced_evaluation.py --category hallucination_fabrication
python scripts/run_enhanced_evaluation.py --category risen_citation
```

**Test Categories:**

| Category | Tests | Purpose |
|----------|-------|---------|
| `cohere_negation` | 8 | Cross-encoder negation understanding |
| `cohere_contradiction` | 4 | Premise contradiction detection |
| `hallucination_fabrication` | 5 | Prevent inventing policies |
| `hallucination_extrapolation` | 3 | Prevent speculation |
| `risen_role` | 4 | RAG-only, no opinions |
| `risen_citation` | 3 | Mandatory citation compliance |
| `risen_refusal` | 3 | Safety bypass refusal |
| `risen_adversarial` | 5 | Jailbreak resistance |
| `risen_unclear` | 4 | Gibberish/typo handling |
| `safety_critical` | 4 | Life-safety accuracy |
| `verbatim_accuracy` | 4 | Exact numbers/timeframes |

---

## Running Tests Locally

### Prerequisites

```bash
# Backend
cd apps/backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio

# Frontend
cd apps/frontend
npm install
```

### Environment Setup

Tests require environment variables. Create `apps/backend/.env`:

```bash
# Minimum for unit tests (many tests mock Azure services)
SEARCH_ENDPOINT=https://test.search.windows.net
SEARCH_API_KEY=test-key
AOAI_ENDPOINT=https://test.openai.azure.com/
AOAI_API_KEY=test-key
```

### Run Full Test Suite

```bash
# Backend
cd apps/backend
pytest tests/ -v --tb=short

# Frontend
cd apps/frontend
npm run check && npm run lint && npm run build
```

---

## CI/CD Tests

GitHub Actions runs on every PR (`.github/workflows/ci.yml`):

1. **Backend Linting**: Black, isort, flake8
2. **Backend Tests**: pytest with coverage
3. **Frontend TypeScript**: `npm run check`
4. **Frontend Lint**: `npm run lint`
5. **Frontend Build**: `npm run build`
6. **CodeQL**: Security analysis

---

## Writing Tests

### Backend Test Pattern

```python
# tests/test_example.py
import pytest
from unittest.mock import Mock, patch

@pytest.fixture
def mock_search_index():
    """Fixture for mocked search index."""
    mock = Mock()
    mock.search.return_value = [...]
    return mock

def test_search_returns_results(mock_search_index):
    """Test that search returns expected results."""
    results = mock_search_index.search("test query")
    assert len(results) > 0

@pytest.mark.asyncio
async def test_async_chat():
    """Test async chat endpoint."""
    # ... async test code
```

### Test Naming Convention

- `test_<function_name>_<scenario>` for unit tests
- `test_<feature>_<expected_behavior>` for integration tests
- Example: `test_chat_service_returns_citations`

---

## Coverage Requirements

| Area | Minimum Coverage |
|------|------------------|
| Core services | 70% |
| Security code | 90% |
| New features | 80% |

Generate coverage report:

```bash
pytest tests/ --cov=app --cov-report=html
open htmlcov/index.html
```

---

## Evaluation Results

Test results are saved to:

- `test_results.json` - Basic test results
- `enhanced_evaluation_results.json` - Full evaluation report

View summary:

```bash
cat enhanced_evaluation_results.json | jq '.report.summary'
```

---

## Troubleshooting Tests

### Tests Failing with Import Errors

```bash
# Ensure you're in the right directory
cd apps/backend

# Activate virtual environment
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

### Tests Timing Out

```bash
# Increase timeout
pytest tests/ -v --timeout=60

# Run single test to debug
pytest tests/test_chat_service.py::test_specific -v -s
```

### Mock Issues

```python
# Ensure correct patch target
@patch('app.services.chat_service.PolicySearchIndex')  # Full path
def test_with_mock(mock_index):
    ...
```

---

## Performance Benchmarks

Run performance tests:

```bash
python scripts/measure_backend_performance.py
```

Expected baselines:
- Cold start: < 5 seconds
- Warm query: < 2 seconds
- Cache hit: < 100ms

See `docs/baselines/` for historical performance data.
