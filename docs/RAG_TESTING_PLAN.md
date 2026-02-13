# RAG Testing Plan: Pre-Production & Weekly Production Monitoring

## Executive Summary

This document defines the comprehensive testing strategy for the RUSH Policy RAG system using **100 realistic staff questions** derived from actual policy content. The framework implements:

1. **Pre-Production Testing**: 4-gate release process with criticality-ordered tests
2. **Weekly Production Monitoring**: Stratified sampling with trend analysis and email alerts

### Key Metrics (Healthcare-Calibrated)

| Metric | Pre-Prod Gate | Weekly Alert | Purpose |
|--------|---------------|--------------|---------|
| Faithfulness | ≥0.85 | <0.80 | Response grounded in context |
| Answer Relevancy | ≥0.70 | <0.65 | Answer addresses question |
| Context Precision | ≥0.60 | <0.55 | Retrieved chunks are relevant |
| Context Recall | ≥0.70 | <0.65 | All relevant chunks retrieved |
| Retrieval Hit Rate | ≥80% | <75% | Correct policy in top-5 |
| Hallucination Rate | ≤5% | >10% | No fabricated policies |
| Citation Accuracy | ≥80% | <75% | Correct policy citations |

---

## Test Dataset Inventory

### Active Test Datasets

| File | Cases | Purpose | Used For |
|------|-------|---------|----------|
| `realistic_staff_questions.json` | 100 | Master dataset from policy review | Source of truth |
| `deepeval_test_dataset_v2.json` | 125 | DeepEval CI/CD (25 original + 100 realistic) | Pre-prod CI/CD |
| `preprod_realistic_tests.json` | 100 | Criticality-ordered (critical→low) | Pre-prod validation |
| `weekly_eval_queries.json` | 50 | Stratified sample across categories | Weekly monitoring |
| `test_dataset_v3.json` | 22 | Synthetic tests | Synthetic coverage |

### Category-Specific Test Batches

| File | Tests | Category | Criticality |
|------|-------|----------|-------------|
| `test_batch_emergency_codes.json` | 15 | Emergency response (Code Blue/Orange/Black) | Critical |
| `test_batch_medications_pharmacy.json` | 15 | High-alert meds, controlled substances | Critical |
| `test_batch_pain_management_epidural.json` | 10 | Epidural management, Narcan dosing | Critical |
| `test_batch_dnr_end_of_life.json` | 10 | DNR orders, advance directives | Critical |
| `test_batch_ed_triage.json` | 10 | ESI triage, standing orders | High |
| `test_batch_scope_of_practice.json` | 10 | MA/CNA/RN boundaries | High |
| `test_batch_urinary_catheter_cauti.json` | 10 | CAUTI prevention, catheter care | High |
| `test_batch_infection_control.json` | 10 | Isolation, scabies/lice, CHG bathing | High |
| `test_batch_safety_security.json` | 10 | Weapons, workplace violence | Medium |

### Test Type Distribution

| Test Type | Count | Purpose |
|-----------|-------|---------|
| `retrieval_accuracy` | 60 | Standard policy retrieval |
| `safety_critical` | 15 | Verbatim accuracy (dosing, phone numbers) |
| `negation_handling` | 12 | Cohere rerank negation tests |
| `scope_boundary` | 8 | Role/authority questions |
| `procedural_steps` | 5 | Multi-step procedures |

---

## Pre-Production Testing Framework

### Gate 1: Retrieval Quality

**Purpose**: Validate that the retrieval system finds correct policies.

```bash
# Run retrieval-focused tests
pytest apps/backend/tests/test_rag_evaluation.py::TestRetrievalAccuracy -v
```

**Pass Criteria**:
- Context Precision ≥ 0.60
- Context Recall ≥ 0.70
- 80%+ of queries return correct policy in top-5

**Test Cases**: All 100 realistic questions

### Gate 2: Generation Quality

**Purpose**: Validate answer quality and groundedness.

```bash
# Run DeepEval faithfulness and relevancy tests
pytest apps/backend/tests/test_rag_evaluation.py::TestFaithfulness -v
pytest apps/backend/tests/test_rag_evaluation.py::TestRetrievalAccuracy -v
```

**Pass Criteria**:
- Faithfulness ≥ 0.85
- Answer Relevancy ≥ 0.70
- No critical test failures

**Test Cases**: 125 cases from `deepeval_test_dataset_v2.json`

### Gate 3: Safety & Compliance

**Purpose**: Validate safety-critical accuracy and RISEN compliance.

```bash
# Run safety and compliance tests
pytest apps/backend/tests/test_rag_evaluation.py::TestSafetyCritical -v
pytest apps/backend/tests/test_rag_evaluation.py::TestHallucinationPrevention -v
pytest apps/backend/tests/test_rag_evaluation.py::TestNegationHandling -v
pytest apps/backend/tests/test_rag_evaluation.py::TestRISENCompliance -v
```

**Pass Criteria**:
- 100% pass rate on safety_critical tests
- Hallucination rate ≤ 5%
- All negation tests pass (Cohere rerank validation)
- RISEN compliance: refuses opinions, resists injection

**Critical Test Categories (Must Pass 100%)**:
| Category | Tests | Examples |
|----------|-------|----------|
| Safety Critical | 15 | Phone numbers, Narcan dosing, seclusion limits |
| Emergency Codes | 15 | Code Blue procedure, decontamination steps |
| DNR/End of Life | 10 | DNR during surgery, surrogate decision makers |

### Gate 4: Domain Regression

**Purpose**: Validate no regression in specific policy domains.

```bash
# Run domain-specific regression tests
pytest apps/backend/tests/test_rag_evaluation.py -v -k "emergency or medication or pain"
```

**Pass Criteria**:
- Each domain batch ≥ 80% pass rate
- No new failures in previously passing tests

---

## Weekly Production Monitoring

### Overview

Weekly evaluation uses stratified sampling to ensure coverage across all 9 question categories while detecting performance drift.

### Weekly Workflow

```bash
# Step 1: Generate fresh stratified sample (different each week)
python scripts/integrate_realistic_questions.py \
    --weekly-eval --sample 50 --seed $(date +%W)

# Step 2: Run weekly evaluation with DeepEval metrics
python scripts/weekly_eval.py \
    --local-queries apps/backend/data/weekly_eval_queries.json

# Step 3: Email sent automatically if SMTP configured
# Reports saved to eval_reports/
```

### Sample Distribution

Each weekly sample includes proportional representation:

| Category | Sample Size | Coverage |
|----------|-------------|----------|
| emergency_codes | 8 | 16% |
| medications_pharmacy | 7 | 14% |
| pain_management_epidural | 5 | 10% |
| urinary_catheter_cauti | 5 | 10% |
| infection_control | 5 | 10% |
| dnr_end_of_life | 5 | 10% |
| ed_triage | 5 | 10% |
| scope_of_practice | 5 | 10% |
| safety_security | 5 | 10% |

### Metrics Evaluated

1. **Faithfulness** (DeepEval): Is response grounded in retrieved context?
2. **Answer Relevancy** (DeepEval): Does answer address the question?
3. **Context Precision** (DeepEval): Are retrieved chunks relevant?
4. **Policy Citation** (Custom): Does response cite correct policy?

### Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Faithfulness | <0.80 | <0.75 |
| Answer Relevancy | <0.65 | <0.60 |
| Context Precision | <0.55 | <0.50 |
| Pass Rate | <85% | <80% |

### Trend Analysis

Weekly reports include:
- Week-over-week metric comparison
- 4-week rolling average
- Category-level breakdown
- Failure pattern analysis

---

## CI/CD Integration

### GitHub Actions Workflow

Create `.github/workflows/rag-evaluation.yml`:

```yaml
name: RAG Evaluation

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  schedule:
    # Weekly evaluation every Monday at 6 AM UTC
    - cron: '0 6 * * 1'

env:
  AOAI_ENDPOINT: ${{ secrets.AOAI_ENDPOINT }}
  AOAI_API_KEY: ${{ secrets.AOAI_API_KEY }}
  AOAI_EVAL_DEPLOYMENT: gpt-4.1-mini
  SEARCH_ENDPOINT: ${{ secrets.SEARCH_ENDPOINT }}
  SEARCH_API_KEY: ${{ secrets.SEARCH_API_KEY }}

jobs:
  pre-prod-evaluation:
    if: github.event_name == 'pull_request' || github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          cd apps/backend
          pip install -r requirements.txt

      - name: Run DeepEval Tests
        run: |
          cd apps/backend
          pytest tests/test_rag_evaluation.py -v --tb=short \
            --junitxml=reports/junit.xml
        env:
          DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE: 240

      - name: Upload Test Results
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-results
          path: apps/backend/reports/

  weekly-evaluation:
    if: github.event_name == 'schedule'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          cd apps/backend
          pip install -r requirements.txt

      - name: Generate Weekly Sample
        run: |
          python scripts/integrate_realistic_questions.py \
            --weekly-eval --sample 50 --seed $(date +%W)

      - name: Run Weekly Evaluation
        run: |
          python scripts/weekly_eval.py \
            --local-queries apps/backend/data/weekly_eval_queries.json
        env:
          SMTP_SERVER: ${{ secrets.SMTP_SERVER }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}

      - name: Upload Weekly Report
        uses: actions/upload-artifact@v4
        with:
          name: weekly-report
          path: eval_reports/
```

---

## Commands Reference

### Pre-Production Testing

```bash
# Pre-prod validation
pytest apps/backend/tests/test_rag_evaluation.py -v  # Gates 1-3

# Domain-specific regression
pytest apps/backend/tests/test_rag_evaluation.py -v -k "emergency"
```

### Weekly Monitoring

```bash
# Generate + run weekly evaluation
python scripts/integrate_realistic_questions.py --weekly-eval --sample 50
python scripts/weekly_eval.py --local-queries apps/backend/data/weekly_eval_queries.json

# Dry run (no email)
python scripts/weekly_eval.py --local-queries apps/backend/data/weekly_eval_queries.json --dry-run

# View latest report
cat eval_reports/eval_report_*.json | jq '.summary'
```

### Test Dataset Management

```bash
# List available categories
python scripts/integrate_realistic_questions.py --list-categories

# Export all formats
python scripts/integrate_realistic_questions.py --export-all

# Regenerate weekly sample with specific seed
python scripts/integrate_realistic_questions.py --weekly-eval --sample 50 --seed 42

# Create category batch
python scripts/integrate_realistic_questions.py --category emergency_codes
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| DeepEval timeout | Token limit exceeded | Use `gpt-4.1-mini` for eval model |
| Low faithfulness | Missing context | Check retrieval top_k setting |
| Negation failures | Cohere rerank disabled | Set `USE_COHERE_RERANK=true` |
| Citation missing | Response format issue | Check RISEN prompt template |

### Debugging Commands

```bash
# Check backend health
curl -s http://localhost:8000/health | jq

# Test single query
curl -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "What is the code blue policy?"}'

# Run with verbose logging
DEEPEVAL_LOG_LEVEL=DEBUG pytest tests/test_rag_evaluation.py -v

# Check metric calculation
python -c "
from app.evaluation.metrics import DeepEvalMetrics
m = DeepEvalMetrics()
print(m.faithfulness_metric.threshold)
"
```

---

## Appendix: Question Examples by Category

### Emergency Codes (Critical)
- "What do I do when a Code Orange is called?"
- "What is the phone number to call for a Code Orange emergency?"
- "How do I decontaminate a patient who was exposed to hazardous materials?"

### Medications (Critical)
- "What medications require dual RN verification?"
- "What is the Narcan dose for epidural-related respiratory depression?"
- "How do I document a medication error?"

### Negation Handling (Cohere Validation)
- "What patients should NOT have their catheters removed by nursing?"
- "Can a CNA administer medications?" (expects "cannot")
- "Can concealed carry permit holders bring guns into the hospital?"

### Safety Critical (Verbatim Required)
- "What blood pressure threshold prevents giving an epidural bolus?"
- "How long can a patient be in time out or seclusion?"
- "What is the rapid response phone number?"

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-01 | Initial comprehensive testing plan |
