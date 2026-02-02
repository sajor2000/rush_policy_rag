# RAG Optimization Sprint Plan

**Created**: 2026-02-01
**Based on**: Cohere Official Docs, DeepEval Best Practices, Azure AI Search Guidance, RAG Techniques Research

---

## Executive Summary

| Current State | Target State | Timeline |
|---------------|--------------|----------|
| Pass Rate: 20% | Pass Rate: 80% | 4 Sprints |
| Context Precision: 0.506 | Context Precision: 0.70 | 2 weeks |
| Policy Citation: 0.639 | Policy Citation: 0.85 | 2 weeks |
| Lost-in-Middle: 16 claims | Lost-in-Middle: <5 | 1 week |

---

## Sprint 1: Quick Wins (P0)
**Duration**: 1 day
**Risk**: Low
**Expected Impact**: +15-20% pass rate

### 1.1 Reduce Cohere Top-N from 5 → 3

**File**: `apps/backend/app/core/config.py:77`

```python
# BEFORE
COHERE_RERANK_TOP_N: int = 5

# AFTER
COHERE_RERANK_TOP_N: int = 3
```

**Rationale** (from Cohere docs):
> "Here we select `top_n` to be 2, which will be the documents we will pass next for response generation."
> — [Cohere RAG Guide](https://docs.cohere.com/v1/docs/rag-with-cohere#reranking-with-rerank)

Research shows 3-5 optimal; reducing to 3 focuses the context window and reduces lost-in-middle effect.

---

### 1.2 Raise Cohere Min Score from 0.40 → 0.50

**File**: `apps/backend/app/core/config.py:80`

```python
# BEFORE
COHERE_RERANK_MIN_SCORE: float = 0.40

# AFTER
COHERE_RERANK_MIN_SCORE: float = 0.50
```

**Rationale** (from Cohere docs):
> "To find a threshold on the scores to determine whether a document is relevant or not, we recommend going through the following process: Select a set of 30-50 representative queries from your domain..."
> — [Cohere Best Practices](https://docs.cohere.com/v1/docs/reranking-best-practices)

Current 0.40 allows too many marginal documents through. 0.50 provides better precision for healthcare.

---

### 1.3 Tighten Score Window Threshold from 0.6 → 0.7

**File**: `apps/backend/app/services/chat_service.py:1351`

```python
# BEFORE
reranked = self.filter_by_score_window(
    reranked,
    request.message,
    window_threshold=0.6
)

# AFTER
reranked = self.filter_by_score_window(
    reranked,
    request.message,
    window_threshold=0.7
)
```

**Rationale**: Keep only documents within 70% of top score (e.g., if top=0.9, keep only docs ≥0.63). This filters tangentially related policies.

---

### 1.4 Validation Commands

```bash
# After making changes, run evaluation
cd apps/backend
python -m pytest tests/test_rag_evaluation.py -v --tb=short

# Quick weekly eval
cd ../..
python scripts/weekly_eval.py --dry-run --sample 25
```

### Sprint 1 Acceptance Criteria

- [ ] `COHERE_RERANK_TOP_N=3` in config.py
- [ ] `COHERE_RERANK_MIN_SCORE=0.50` in config.py
- [ ] `window_threshold=0.7` in chat_service.py
- [ ] Weekly eval shows context_precision ≥ 0.55
- [ ] No regression in faithfulness (stays ≥ 0.85)

---

## Sprint 2: Citation Enforcement (P1)
**Duration**: 2-3 days
**Risk**: Low
**Expected Impact**: +20% policy citation score

### 2.1 Update System Prompt with Citation Requirements

**File**: `apps/backend/policytech_prompt.txt`

Add after the existing RISEN instructions:

```markdown
## CITATION REQUIREMENTS (MANDATORY)

You MUST cite specific policy references for every factual claim:

### Required Citation Format:
- "Per Ref #486, verbal orders must be authenticated within 72 hours."
- "According to the Rapid Response Team Policy (Ref #346), dial 2-5111."
- "The Hand Hygiene Policy (Ref #XXX) requires hand washing before patient contact."

### Citation Rules:
1. Every procedural statement MUST include "Ref #XXX" or full policy title
2. Phone numbers, timeframes, and thresholds MUST cite their source policy
3. If multiple policies apply, cite ALL relevant reference numbers
4. NEVER use vague citations like "per RUSH policy" without the reference number

### When No Reference Found:
If you cannot find a specific reference number, state:
"This information appears in RUSH policies but I cannot confirm the specific reference number."

### Example Responses:

GOOD: "Verbal orders must be authenticated within 72 hours (Ref #486). Only RNs, APPs, and pharmacists may accept verbal orders."

BAD: "According to RUSH policy, verbal orders must be authenticated within 72 hours."
```

**Rationale** (from Microsoft Healthcare Guidance):
> "Groundedness – is the answer based on the retrieved evidence? Evidence Relevance – are the selected evidence sources and provided links relevant to the question asked."
> — [Microsoft Health Bot Transparency Note](https://learn.microsoft.com/en-us/azure/health-bot/transparency-note)

---

### 2.2 Update Response Formatter to Enforce Citations

**File**: `apps/backend/app/services/response_formatter.py`

Add validation function:

```python
import re
from typing import Tuple

def validate_citation_compliance(response: str, evidence: list) -> Tuple[bool, str]:
    """
    Validate that response contains proper policy citations.

    Returns:
        Tuple of (is_compliant, warning_message)
    """
    # Check for Ref # pattern
    ref_pattern = r'Ref\s*#\s*\d+'
    refs_found = re.findall(ref_pattern, response, re.IGNORECASE)

    # Check for policy title citations
    title_pattern = r'(?:per|according to|from)\s+(?:the\s+)?([A-Z][^,\.]+Policy|[A-Z][^,\.]+Procedure)'
    titles_found = re.findall(title_pattern, response, re.IGNORECASE)

    has_citations = len(refs_found) > 0 or len(titles_found) > 0

    if not has_citations and len(response) > 100:
        return False, "Response lacks policy citations"

    return True, ""


def inject_citations_reminder(context: str) -> str:
    """
    Add citation reminder to context sent to LLM.
    """
    reminder = """
REMINDER: You MUST cite policy reference numbers (Ref #XXX) for every factual claim.
"""
    return reminder + context
```

---

### 2.3 Add Citation Validation to Chat Service

**File**: `apps/backend/app/services/chat_service.py`

In the response generation flow, add validation:

```python
from app.services.response_formatter import validate_citation_compliance

# After generating response, validate citations
is_compliant, warning = validate_citation_compliance(response_text, evidence_items)
if not is_compliant:
    logger.warning(f"Citation compliance issue: {warning}")
    # Optionally append citation reminder to safety_flags
    safety_flags.append("LOW_CITATION_DENSITY")
```

### Sprint 2 Acceptance Criteria

- [ ] System prompt updated with citation requirements
- [ ] Citation validation function implemented
- [ ] Logging for low-citation responses
- [ ] Weekly eval shows policy_citation ≥ 0.75
- [ ] Manual review of 10 responses confirms Ref # presence

---

## Sprint 3: Dynamic Thresholds (P2)
**Duration**: 3-4 days
**Risk**: Medium
**Expected Impact**: +10% pass rate on edge cases

### 3.1 Create Query Classifier for Dynamic Thresholds

**File**: `apps/backend/app/services/query_classifier.py` (NEW)

```python
"""
Query classification for dynamic Cohere threshold selection.

Based on Cohere best practices and DeepEval RAG evaluation guidance.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class CohereConfig:
    """Dynamic Cohere configuration based on query type."""
    top_n: int
    min_score: float
    window_threshold: float
    query_type: str


# Query patterns for classification
NEGATION_PATTERNS = [
    r'\bnot\b', r'\bcannot\b', r"\bcan't\b", r'\bprohibited\b',
    r'\bunauthorized\b', r'\bforbidden\b', r'\bnever\b',
    r'\bwho\s+(?:is|are)\s+not\b', r'\bwhat\s+(?:is|are)\s+not\b'
]

EXACT_VALUE_PATTERNS = [
    r'\bphone\s*(?:number)?\b', r'\bdial\b', r'\bcall\b',
    r'\bdose\b', r'\bmg\b', r'\bml\b', r'\bthreshold\b',
    r'\bscore\b', r'\btimeframe\b', r'\bhours?\b', r'\bminutes?\b',
    r'\bwithin\s+\d+', r'\bexact\b', r'\bspecific\b'
]

MULTI_POLICY_PATTERNS = [
    r'\ball\b', r'\bevery\b', r'\blist\b', r'\bwhat\s+are\s+the\b',
    r'\bdifferent\b', r'\bvarious\b', r'\bmultiple\b',
    r'\bcompare\b', r'\bboth\b'
]

ENTITY_SPECIFIC_PATTERNS = [
    r'\bRUMC\b', r'\bRUMG\b', r'\bROPH\b', r'\bRCMC\b', r'\bRMG\b',
    r'\bRush\s+(?:Oak\s+Park|Copley|University|Medical)\b',
    r'\bpediatric\b', r'\badult\b', r'\bneonatal\b', r'\bNICU\b'
]


def _matches_any_pattern(text: str, patterns: List[str]) -> bool:
    """Check if text matches any of the given regex patterns."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def classify_query(query: str) -> CohereConfig:
    """
    Classify query and return optimal Cohere configuration.

    Based on:
    - Cohere Best Practices: threshold calibration per domain
    - DeepEval: ContextualPrecisionMetric evaluation
    - Lost-in-middle research: optimal top_n is 2-5

    Args:
        query: User query string

    Returns:
        CohereConfig with optimized parameters
    """
    query_lower = query.lower().strip()
    word_count = len(query.split())

    # Priority 1: Negation queries need HIGH precision
    # These ask "what is NOT allowed" - wrong docs are dangerous
    if _matches_any_pattern(query, NEGATION_PATTERNS):
        return CohereConfig(
            top_n=3,
            min_score=0.55,
            window_threshold=0.75,
            query_type="negation"
        )

    # Priority 2: Exact value queries (phone numbers, doses, thresholds)
    # Wrong numbers can be life-threatening in healthcare
    if _matches_any_pattern(query, EXACT_VALUE_PATTERNS):
        return CohereConfig(
            top_n=2,  # Very focused - one authoritative source
            min_score=0.55,
            window_threshold=0.80,
            query_type="exact_value"
        )

    # Priority 3: Multi-policy queries need broader coverage
    if _matches_any_pattern(query, MULTI_POLICY_PATTERNS):
        return CohereConfig(
            top_n=7,
            min_score=0.35,
            window_threshold=0.50,
            query_type="multi_policy"
        )

    # Priority 4: Entity-specific queries
    if _matches_any_pattern(query, ENTITY_SPECIFIC_PATTERNS):
        return CohereConfig(
            top_n=5,
            min_score=0.45,
            window_threshold=0.65,
            query_type="entity_specific"
        )

    # Priority 5: Very short queries (≤3 words)
    if word_count <= 3:
        return CohereConfig(
            top_n=3,
            min_score=0.50,
            window_threshold=0.70,
            query_type="short"
        )

    # Default: Standard queries
    return CohereConfig(
        top_n=3,
        min_score=0.50,
        window_threshold=0.70,
        query_type="standard"
    )


def get_cohere_params(query: str) -> dict:
    """
    Convenience function returning dict of Cohere parameters.

    Usage:
        params = get_cohere_params("Who is NOT authorized to accept verbal orders?")
        reranked = await cohere_service.rerank_async(
            query=query,
            documents=docs,
            top_n=params['top_n'],
            min_score=params['min_score']
        )
    """
    config = classify_query(query)
    return {
        'top_n': config.top_n,
        'min_score': config.min_score,
        'window_threshold': config.window_threshold,
        'query_type': config.query_type
    }
```

---

### 3.2 Integrate Query Classifier into Chat Service

**File**: `apps/backend/app/services/chat_service.py`

Replace static threshold usage with dynamic classifier:

```python
from app.services.query_classifier import get_cohere_params, classify_query

# In _chat_with_cohere_rerank method:

# BEFORE (around line 1315)
dynamic_top_n = self._get_cohere_top_n(request.message)
reranked = await self.cohere_rerank_service.rerank_async(
    query=request.message,
    documents=docs_for_rerank,
    top_n=dynamic_top_n,
    min_score=settings.COHERE_RERANK_MIN_SCORE
)

# AFTER
cohere_params = get_cohere_params(request.message)
logger.info(f"Query classified as '{cohere_params['query_type']}': top_n={cohere_params['top_n']}, min_score={cohere_params['min_score']}")

reranked = await self.cohere_rerank_service.rerank_async(
    query=request.message,
    documents=docs_for_rerank,
    top_n=cohere_params['top_n'],
    min_score=cohere_params['min_score']
)

# Also update score windowing to use dynamic threshold
if reranked and len(reranked) > 3:
    reranked = self.filter_by_score_window(
        reranked,
        request.message,
        window_threshold=cohere_params['window_threshold']
    )
```

---

### 3.3 Add Unit Tests for Query Classifier

**File**: `apps/backend/tests/test_query_classifier.py` (NEW)

```python
"""Unit tests for query classifier."""

import pytest
from app.services.query_classifier import classify_query, get_cohere_params, CohereConfig


class TestQueryClassifier:
    """Test query classification logic."""

    def test_negation_query_high_precision(self):
        """Negation queries should use high precision settings."""
        queries = [
            "Who is NOT authorized to accept verbal orders?",
            "What medications cannot be given via NG tube?",
            "When is it prohibited to use restraints?",
        ]
        for query in queries:
            config = classify_query(query)
            assert config.query_type == "negation"
            assert config.min_score >= 0.55
            assert config.top_n <= 3

    def test_exact_value_query_very_focused(self):
        """Exact value queries should be very focused."""
        queries = [
            "What is the rapid response phone number?",
            "What is the dose for epinephrine?",
            "What MEWS score triggers RRT?",
        ]
        for query in queries:
            config = classify_query(query)
            assert config.query_type == "exact_value"
            assert config.top_n <= 2
            assert config.min_score >= 0.55

    def test_multi_policy_query_broader(self):
        """Multi-policy queries should allow broader coverage."""
        queries = [
            "What are all the isolation precautions?",
            "List the different code types at RUSH",
            "Compare fall prevention policies",
        ]
        for query in queries:
            config = classify_query(query)
            assert config.query_type == "multi_policy"
            assert config.top_n >= 5
            assert config.min_score <= 0.40

    def test_short_query_standard_settings(self):
        """Short queries should use standard focused settings."""
        config = classify_query("code blue")
        assert config.query_type == "short"
        assert config.top_n == 3

    def test_get_cohere_params_returns_dict(self):
        """get_cohere_params should return usable dict."""
        params = get_cohere_params("verbal orders policy")
        assert 'top_n' in params
        assert 'min_score' in params
        assert 'window_threshold' in params
        assert 'query_type' in params
```

### Sprint 3 Acceptance Criteria

- [ ] `query_classifier.py` implemented with all patterns
- [ ] Chat service uses dynamic thresholds
- [ ] Unit tests pass for all query types
- [ ] Logging shows query classification in action
- [ ] Negation queries show improved accuracy
- [ ] Weekly eval shows +5% improvement on edge cases

---

## Sprint 4: Threshold Calibration & Compression (P3)
**Duration**: 1 week
**Risk**: Medium
**Expected Impact**: +10-15% overall improvement

### 4.1 Create Calibration Dataset (Cohere Recommended)

**File**: `apps/backend/data/calibration_dataset.json` (NEW)

Per Cohere's official guidance, create 30-50 borderline-relevant pairs:

```json
{
  "calibration_pairs": [
    {
      "query": "What is the verbal orders policy?",
      "borderline_doc": "Policy about telephone communication protocols...",
      "expected_relevance": "borderline",
      "notes": "Related to communication but not specifically verbal orders"
    },
    {
      "query": "Code blue phone number",
      "borderline_doc": "Emergency codes overview and general procedures...",
      "expected_relevance": "borderline",
      "notes": "Mentions codes but not specific phone number"
    },
    {
      "query": "Hand hygiene requirements",
      "borderline_doc": "Infection control general principles...",
      "expected_relevance": "borderline",
      "notes": "Related to infection control but not specific to hand hygiene"
    }
  ]
}
```

---

### 4.2 Calibration Script

**File**: `scripts/calibrate_cohere_threshold.py` (NEW)

```python
#!/usr/bin/env python3
"""
Cohere threshold calibration script.

Based on official Cohere recommendation:
"Select a set of 30-50 representative queries from your domain.
For each query provide a document that is considered borderline relevant."

Usage:
    python scripts/calibrate_cohere_threshold.py
"""

import asyncio
import json
import statistics
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "apps/backend"))

from app.services.cohere_rerank_service import CohereRerankService
from app.core.config import settings


async def calibrate_threshold():
    """
    Run calibration to find optimal min_score threshold.

    Process:
    1. Load borderline-relevant (query, doc) pairs
    2. Score each pair through Cohere
    3. Average scores = recommended threshold
    """
    # Load calibration dataset
    calibration_path = Path(__file__).parent.parent / "apps/backend/data/calibration_dataset.json"

    if not calibration_path.exists():
        print("ERROR: calibration_dataset.json not found")
        print("Please create the file with 30-50 borderline-relevant pairs")
        return

    with open(calibration_path) as f:
        data = json.load(f)

    pairs = data.get("calibration_pairs", [])
    if len(pairs) < 30:
        print(f"WARNING: Only {len(pairs)} pairs. Cohere recommends 30-50.")

    # Initialize Cohere service
    service = CohereRerankService(
        endpoint=settings.COHERE_RERANK_ENDPOINT,
        api_key=settings.COHERE_RERANK_API_KEY,
        top_n=1,  # We only need top score
        min_score=0.0,  # Don't filter - we want raw scores
        model_name=settings.COHERE_RERANK_MODEL
    )

    scores = []

    print(f"Calibrating with {len(pairs)} borderline-relevant pairs...")
    print("-" * 60)

    for i, pair in enumerate(pairs):
        query = pair["query"]
        doc = pair["borderline_doc"]

        try:
            results = await service.rerank_async(
                query=query,
                documents=[{"content": doc, "title": "Calibration Doc"}],
                top_n=1,
                min_score=0.0
            )

            if results:
                score = results[0].cohere_score
                scores.append(score)
                print(f"[{i+1}/{len(pairs)}] Score: {score:.4f} | Query: {query[:50]}...")
            else:
                print(f"[{i+1}/{len(pairs)}] No result for: {query[:50]}...")

        except Exception as e:
            print(f"[{i+1}/{len(pairs)}] Error: {e}")

    if not scores:
        print("ERROR: No scores collected")
        return

    # Calculate statistics
    avg_score = statistics.mean(scores)
    median_score = statistics.median(scores)
    std_dev = statistics.stdev(scores) if len(scores) > 1 else 0
    min_score = min(scores)
    max_score = max(scores)

    print("\n" + "=" * 60)
    print("CALIBRATION RESULTS")
    print("=" * 60)
    print(f"Samples:          {len(scores)}")
    print(f"Average Score:    {avg_score:.4f}")
    print(f"Median Score:     {median_score:.4f}")
    print(f"Std Deviation:    {std_dev:.4f}")
    print(f"Min Score:        {min_score:.4f}")
    print(f"Max Score:        {max_score:.4f}")
    print("-" * 60)
    print(f"RECOMMENDED THRESHOLD: {avg_score:.2f}")
    print("-" * 60)
    print("\nTo apply this threshold, update config.py:")
    print(f"  COHERE_RERANK_MIN_SCORE: float = {avg_score:.2f}")

    # Save results
    results = {
        "samples": len(scores),
        "average": avg_score,
        "median": median_score,
        "std_dev": std_dev,
        "min": min_score,
        "max": max_score,
        "recommended_threshold": round(avg_score, 2),
        "all_scores": scores
    }

    output_path = Path(__file__).parent.parent / "eval_reports/calibration_results.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(calibrate_threshold())
```

---

### 4.3 Contextual Compression (Optional Advanced)

**File**: `apps/backend/app/services/context_compressor.py` (NEW)

Based on LangChain's contextual compression pattern:

```python
"""
Contextual compression for RAG context optimization.

Extracts only query-relevant content from retrieved chunks,
reducing noise and improving LLM focus.

Reference: LangChain ContextualCompressionRetriever
"""

import logging
from typing import List, Optional
from openai import AzureOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)


class ContextCompressor:
    """
    Compress retrieved context to query-relevant content only.

    This addresses the "lost in middle" problem by reducing
    context size while preserving relevant information.
    """

    COMPRESSION_PROMPT = """Extract ONLY the sentences from this policy text that directly answer the question.
Do not add any commentary or explanation.
If no sentences are relevant, respond with "NO_RELEVANT_CONTENT".

Question: {query}

Policy Text:
{chunk}

Relevant sentences:"""

    def __init__(self, client: Optional[AzureOpenAI] = None):
        """Initialize with Azure OpenAI client."""
        self.client = client or AzureOpenAI(
            azure_endpoint=settings.AOAI_ENDPOINT,
            api_key=settings.AOAI_API_KEY,
            api_version="2024-02-15-preview"
        )
        self.model = settings.AOAI_CHAT_DEPLOYMENT

    async def compress_chunks(
        self,
        query: str,
        chunks: List[str],
        max_compressed_length: int = 2000
    ) -> List[str]:
        """
        Compress multiple chunks to query-relevant content.

        Args:
            query: User query
            chunks: List of retrieved chunk texts
            max_compressed_length: Max chars per compressed chunk

        Returns:
            List of compressed chunks (may be shorter than input)
        """
        compressed = []

        for chunk in chunks:
            try:
                result = await self._compress_single(query, chunk)
                if result and result != "NO_RELEVANT_CONTENT":
                    # Truncate if too long
                    if len(result) > max_compressed_length:
                        result = result[:max_compressed_length] + "..."
                    compressed.append(result)
            except Exception as e:
                logger.warning(f"Compression failed for chunk: {e}")
                # Fall back to original chunk
                compressed.append(chunk[:max_compressed_length])

        logger.info(f"Compressed {len(chunks)} chunks → {len(compressed)} relevant chunks")
        return compressed

    async def _compress_single(self, query: str, chunk: str) -> Optional[str]:
        """Compress a single chunk."""
        prompt = self.COMPRESSION_PROMPT.format(query=query, chunk=chunk)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500
        )

        return response.choices[0].message.content.strip()


# Singleton instance
_compressor: Optional[ContextCompressor] = None


def get_context_compressor() -> ContextCompressor:
    """Get singleton compressor instance."""
    global _compressor
    if _compressor is None:
        _compressor = ContextCompressor()
    return _compressor
```

---

### 4.4 Long Context Reordering Enhancement

Based on the research paper "Lost in the Middle" and LangChain's `LongContextReorder`:

**File**: `apps/backend/app/services/chat_service.py`

Verify the `_reorder_for_attention` method places docs optimally:

```python
def _reorder_for_attention(self, reranked: List[RerankResult]) -> List[RerankResult]:
    """
    Reorder documents to mitigate lost-in-middle attention decay.

    Research: "No matter the architecture of your model, there is a
    substantial performance degradation when you include 10+ retrieved
    documents. When models must access relevant information in the middle
    of long contexts, they tend to ignore the provided documents."
    — arxiv.org/abs/2307.03172

    Strategy: U-shaped attention curve
    - Position 0: Best doc (primacy effect)
    - Position -1: 2nd best doc (recency effect)
    - Middle: Lower-ranked docs (attention decay zone)

    Example with 5 docs [1,2,3,4,5] by relevance:
    Output: [1, 3, 5, 4, 2]
            ^  ^  ^  ^  ^
           P0 P1 P2 P3 END
    """
    if len(reranked) <= 2:
        return reranked

    n = len(reranked)
    reordered = [None] * n

    front_idx, back_idx = 0, n - 1
    for i, doc in enumerate(reranked):
        if i % 2 == 0:
            reordered[front_idx] = doc
            front_idx += 1
        else:
            reordered[back_idx] = doc
            back_idx -= 1

    logger.debug(
        f"Reordered {n} docs: best→pos0, 2nd_best→pos{n-1}, 3rd→pos1"
    )
    return reordered
```

### Sprint 4 Acceptance Criteria

- [ ] Calibration dataset created (30-50 pairs)
- [ ] Calibration script runs successfully
- [ ] New threshold applied based on calibration
- [ ] Context compressor implemented (optional)
- [ ] Reordering verified for lost-in-middle mitigation
- [ ] Final eval shows ≥80% pass rate OR clear path to target

---

## Validation & Monitoring

### Evaluation Commands

```bash
# Quick validation (after each sprint)
python scripts/weekly_eval.py --dry-run --sample 25

# Full evaluation
python scripts/weekly_eval.py

# DeepEval CI/CD tests
cd apps/backend
pytest tests/test_rag_evaluation.py -v

# Enhanced evaluation (all categories)
python scripts/run_enhanced_evaluation.py
```

### Key Metrics to Track

| Metric | Sprint 1 Target | Sprint 2 Target | Sprint 3 Target | Sprint 4 Target |
|--------|-----------------|-----------------|-----------------|-----------------|
| Pass Rate | 35% | 50% | 60% | 80% |
| Context Precision | 0.55 | 0.60 | 0.65 | 0.70 |
| Policy Citation | 0.65 | 0.75 | 0.80 | 0.85 |
| Faithfulness | ≥0.85 | ≥0.85 | ≥0.85 | ≥0.90 |
| Lost-in-Middle | 12 | 8 | 5 | <3 |

### Logging Queries for Analysis

Add to chat_service.py for monitoring:

```python
logger.info(
    f"COHERE_METRICS: "
    f"query_type={cohere_params['query_type']} "
    f"top_n={cohere_params['top_n']} "
    f"min_score={cohere_params['min_score']} "
    f"docs_returned={len(reranked)} "
    f"top_score={reranked[0].cohere_score if reranked else 0:.3f}"
)
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Over-filtering (too aggressive thresholds) | Keep fallback tiers (0.05, 0.0) |
| Regression in faithfulness | Monitor faithfulness metric each sprint |
| Citation prompt increases latency | Test response times before/after |
| Query classifier misclassification | Log classifications, review edge cases |

---

## Summary

| Sprint | Focus | Files Changed | Expected Impact |
|--------|-------|---------------|-----------------|
| **Sprint 1** | Config tuning | config.py, chat_service.py | +15-20% pass rate |
| **Sprint 2** | Citation enforcement | policytech_prompt.txt, response_formatter.py | +20% citation score |
| **Sprint 3** | Dynamic thresholds | query_classifier.py (NEW), chat_service.py | +10% edge cases |
| **Sprint 4** | Calibration & compression | calibration script, context_compressor.py | +10-15% overall |

**Total Expected Improvement**: 20% → 80% pass rate
