# RAG Architecture Design

This document explains the design decisions behind the RUSH Policy RAG Agent's retrieval-augmented generation pipeline.

## Overview

The RAG pipeline is optimized for healthcare policy retrieval where:
- **Accuracy is paramount** - Patient safety depends on correct policy information
- **Negation matters** - "NOT authorized" must contradict "Can X do Y?"
- **Complete context is essential** - Procedural steps must not be truncated

## Architecture Diagram

```
User Query
    │
    ▼
┌─────────────────────────┐
│   Synonym Expansion     │  ← Two-layer (query-time + index-time)
│   (SynonymService)      │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Azure AI Search       │  ← vectorSemanticHybrid
│   (100+ candidates)     │     Vector + BM25 + L2 reranking
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Cohere Rerank 4.0     │  ← Cross-encoder (negation-aware)
│   (Top 5, min 0.40)     │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Context Expansion     │  ← Sibling chunk retrieval
│   (±1 chunks)           │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Azure OpenAI GPT-4.1  │  ← RISEN prompt framework
│   (Chat Completions)    │
└───────────┬─────────────┘
            │
            ▼
      Response with Citations
```

## 1. Retrieval Strategy: vectorSemanticHybrid

**Decision:** Use Azure AI Search with `vectorSemanticHybrid` query type.

**Why this approach:**

| Method | Pros | Cons |
|--------|------|------|
| Vector-only | Great for semantic similarity | Misses exact terminology |
| BM25-only | Great for exact matches | Misses synonyms, context |
| **Hybrid** | Best of both worlds | Slightly more complex |

**Our configuration:**
- **Vector search**: `text-embedding-3-large` (3072 dimensions)
- **Keyword search**: BM25 with 132 synonym rules
- **L2 Semantic Reranking**: Azure's built-in semantic ranker

**Code location:** `apps/backend/app/services/on_your_data_service.py`

## 2. Reranking Strategy: Cohere 4.0 Pro

**Decision:** Add Cohere Rerank 4.0 Pro as a cross-encoder reranker after Azure search.

**Why cross-encoder beats bi-encoder for healthcare:**

```
Query: "Can a Medical Assistant accept verbal orders?"

Bi-encoder (Azure L2):
- Embeds query separately from documents
- Sees vocabulary overlap: "Medical Assistant", "verbal orders"
- May rank "MA can accept verbal orders" highly (wrong!)

Cross-encoder (Cohere):
- Processes query + document together
- Understands "NOT authorized" contradicts the query
- Correctly ranks "MA is NOT authorized to accept verbal orders" as relevant
```

**Configuration:**
| Setting | Value | Rationale |
|---------|-------|-----------|
| Model | `Cohere-rerank-v4.0-pro` | Cross-encoder for negation-aware retrieval |
| Top N | 5 | Optimal for "lost in middle" mitigation |
| Min Score | 0.40 | Healthcare-calibrated threshold |
| Doc Format | YAML | Field ordering matters for Cohere |

**Field ordering (per Cohere best practices):**
```yaml
title: Policy Title
reference_number: Ref #123
section: Section Name
content: Actual policy text...
```

**Code location:** `apps/backend/app/services/cohere_rerank_service.py`

## 3. Prompt Engineering: RISEN Framework

**Decision:** Use the RISEN prompt framework for strict RAG behavior.

The prompt is in `apps/backend/policytech_prompt.txt`:

| Component | Purpose |
|-----------|---------|
| **R**ole | "PolicyTech" - strict RAG system, accuracy over satisfaction |
| **I**nstructions | Only answer from knowledge base, never fabricate |
| **S**teps | Search → Extract verbatim → Cite with metadata |
| **E**nd Goal | Comprehensive answer (3-6 sentences) with citations |
| **N**arrowing | RAG-only, refuse non-policy questions, no hallucinations |

**Key anti-hallucination rules:**
1. ONLY cite policies that appear in search results
2. NEVER invent policy names or reference numbers
3. Verify citations before including them
4. When in doubt, decline to answer

**Adversarial handling:**
- Refuse bypass/circumvention requests
- Reject role-play and jailbreak attempts
- No policy references in refusal responses

## 4. Context Expansion: Sibling Chunks

**Decision:** Fetch adjacent chunks for top reranked results.

**The problem:** Chunking splits procedural steps across multiple chunks. A query about "Step 3" might retrieve only that chunk, losing context from Steps 1-2-4.

**The solution:** After Cohere reranking, expand context by fetching sibling chunks:

```
Original chunk:        → Expanded context:
[chunk_index: 5]         [chunk 4] + [chunk 5] + [chunk 6]
```

**Configuration:**
| Setting | Value | Rationale |
|---------|-------|-----------|
| Enabled | true | Addresses "lost in middle" |
| Top N to Expand | 3 | Only expand best matches |
| Max Siblings | 1 | ±1 chunks (chunk_index ± 1) |

**Code location:** `apps/backend/app/services/context_expander.py`

## 5. Synonym System: Two-Layer Strategy

**Decision:** Use both query-time expansion AND index-time synonyms.

### Layer 1: Query-Time Expansion

Before search, expand the user query with synonyms:

```
User: "ED code blue policy"
                ↓
Expanded: "ED emergency department code blue cardiac arrest policy"
```

**Benefits:**
- Improves vector embeddings (more semantic coverage)
- Helps BM25 match more terms
- Addresses abbreviation variations

**Code location:** `apps/backend/app/services/synonym_service.py`

### Layer 2: Index-Time Synonyms

Azure AI Search synonym map for BM25 matching:

```
ED, ER, emergency department, emergency room => emergency department
```

**Benefits:**
- Catches cases where documents use different terminology
- Works even if query expansion misses a term

**132 rules across 15 healthcare categories:**
- Emergency codes, departments, procedures
- Medications, equipment, staff roles
- Rush institution terms, compliance terms

**Code location:** `apps/backend/azure_policy_index.py`

## 6. Chunking Strategy: Docling + PyMuPDF

**Decision:** Use dual-library approach with hierarchical chunking.

### PyMuPDF (Primary for Checkboxes)
- Raw text extraction from PDF first page
- Detects "Applies To" checkbox patterns: `☒`, `☐`, `✓`, `✔`, `■`
- Fixes Docling's TableFormer truncation issue

### Docling (Content Extraction)
- TableFormer ACCURATE mode for policy header tables
- Hierarchical chunking: document → section → semantic
- Zero-overlap chunking for literal compliance accuracy

### Chunk Configuration
| Setting | Value | Rationale |
|---------|-------|-----------|
| Target size | ~1,500 chars | Balance context vs. precision |
| Overlap | 0 | Literal accuracy for verbatim quotes |
| Levels | 3 | document, section, semantic |

**Fallback chain:** PyMuPDF → Docling → Regex

**Code location:** `apps/backend/preprocessing/chunker.py`

## 7. Search Index Schema

**38-field schema** optimized for healthcare policy retrieval:

### Core Fields
- `id`, `content`, `content_vector` (3072-dim)
- `title`, `reference_number`, `section`, `citation`
- `applies_to`, `date_updated`, `source_file`

### Entity Boolean Filters (O(1) filtering)
9 boolean fields for multi-tenant filtering:
- `applies_to_rumc`, `applies_to_rumg`, `applies_to_rmg`
- `applies_to_roph`, `applies_to_rcmc`, `applies_to_rch`
- `applies_to_roppg`, `applies_to_rcmg`, `applies_to_ru`

### Hierarchical Chunking Fields
- `chunk_level`: "document" | "section" | "semantic"
- `parent_chunk_id`, `chunk_index`

**Code location:** `apps/backend/azure_policy_index.py`

## 8. Corrective RAG (cRAG)

**Decision:** Apply pre-filtering before cross-encoder reranking to control cost and improve precision.

**File:** `apps/backend/app/services/corrective_rag.py`

The cRAG module classifies retrieved documents into confidence tiers before passing them to Cohere:

- **Correct**: High-confidence matches passed directly
- **Ambiguous**: Medium-confidence documents included up to `CRAG_MAX_AMBIGUOUS_DOCS`
- **Incorrect**: Low-confidence documents filtered out

**Configuration:**
| Setting | Default | Purpose |
|---------|---------|---------|
| `CRAG_MIN_DOCS_FOR_COHERE` | 20 | Guarantees variety in Cohere input |
| `CRAG_MAX_DOCS_FOR_COHERE` | 35 | Bounds Cohere API cost |
| `CRAG_MAX_AMBIGUOUS_DOCS` | 20 | Limits ambiguous document inclusion |

## 9. Self-Reflective RAG

**Decision:** Add a post-generation quality check to detect low-confidence or unsupported answers.

**File:** `apps/backend/app/services/self_reflective_rag.py`

After GPT-4.1 generates a response, the self-reflective module evaluates whether the answer is adequately supported by the retrieved evidence. If confidence is low, the response is flagged for human review.

## 10. Safety Validator

**Decision:** Validate all responses against safety constraints before returning to the user.

**File:** `apps/backend/app/services/safety_validator.py`

Checks include:
- Hallucination detection (response references policies not in search results)
- Out-of-scope content filtering
- Safety flag propagation to the frontend

## 11. Citation Verifier

**Decision:** Verify that every citation in the response maps to an actual search result.

**File:** `apps/backend/app/services/citation_verifier.py`

Post-generation verification ensures:
- Every cited policy title/reference number exists in the retrieved evidence
- No fabricated citations reach the user
- Unverifiable citations are stripped with a warning

## 12. Device Disambiguator

**Decision:** Detect ambiguous medical device queries and prompt for clarification.

**File:** `apps/backend/app/services/device_disambiguator.py`

Medical devices like "IV", "catheter", "line", and "port" have multiple policy contexts (e.g., IV pump vs. IV insertion). The disambiguator detects these terms and triggers a clarification UI in the frontend.

## 13. Entity Ranking

**Decision:** Boost results that match the user's organizational entity context.

**Files:** `apps/backend/app/services/entity_ranking.py`, `apps/backend/app/services/ranking_utils.py`

Post-reranking score adjustments:
- **Location match boost**: Policies matching the user's entity filter get a configurable score boost (`LOCATION_MATCH_BOOST`)
- **Surge capacity penalty**: Surge-level policies are deprioritized (`SURGE_CAPACITY_PENALTY`)
- **Pediatric/Adult context**: Population-aware ranking based on query keywords

## 14. Lost-in-Middle Mitigation

**Decision:** Combine multiple strategies to address the "Lost in the Middle" attention decay problem.

Strategies implemented:
1. **Cohere Top N = 5**: Keeps the context window manageable
2. **Context Expansion on Top 3 only**: Focuses expansion on best matches
3. **RISEN prompt with citation requirements**: Forces the model to attend to all provided evidence
4. **Score windowing**: Post-rerank filtering removes low-relevance tail

**Reference:** [Stanford "Lost in the Middle"](https://arxiv.org/abs/2307.03172)

---

## Performance Metrics

Current test results (as of Feb 2026):

| Metric | Target | Actual |
|--------|--------|--------|
| Test Pass Rate | ≥80% | 88% |
| Faithfulness | ≥0.85 | Achieved |
| Context Recall | ≥0.80 | Achieved |

## Key Files Reference

| Component | File |
|-----------|------|
| RAG orchestrator | `apps/backend/app/services/chat_service.py` |
| Azure OpenAI integration | `apps/backend/app/services/on_your_data_service.py` |
| Cohere reranking | `apps/backend/app/services/cohere_rerank_service.py` |
| Context expansion | `apps/backend/app/services/context_expander.py` |
| Synonym service | `apps/backend/app/services/synonym_service.py` |
| PDF chunking | `apps/backend/preprocessing/chunker.py` |
| Search index | `apps/backend/azure_policy_index.py` |
| RISEN prompt | `apps/backend/policytech_prompt.txt` |

## References

- [Stanford "Lost in the Middle"](https://arxiv.org/abs/2307.03172) - Why position matters in LLM context
- [Cohere Rerank Best Practices](https://docs.cohere.com/docs/reranking-best-practices) - YAML format, field ordering
- [Azure AI Search Hybrid](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview) - vectorSemanticHybrid
- [Docling](https://github.com/DS4SD/docling) - IBM's PDF processing library
