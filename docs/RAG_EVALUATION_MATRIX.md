# RAG Evaluation & Audit Matrix

> Every query and response is evaluated through a layered system of automated gates, scheduled monitoring, and continuous audit logging. This document maps each evaluation mechanism to its context, cadence, and thresholds so that auditors and operators can answer "how is RAG quality measured?" in under 60 seconds.

## Evaluation Contexts

| Context | When | Purpose |
|---------|------|---------|
| **Pre-Production (CI)** | Every PR / push to `main` | Block merges that degrade retrieval quality or introduce regressions |
| **Monthly Release Gate** | Before index promotion (10-stage pipeline) | Block deployment if any quality metric regresses beyond threshold |
| **Weekly Monitoring** | Scheduled (Monday 6 AM UTC) | Detect drift in production query quality before users notice |
| **Daily / Continuous** | Every chat request | Full observability — capture every interaction for analysis |
| **On-Demand** | Manual script execution | Ad-hoc drift detection, baseline persistence, ingestion audits |

---

## Evaluation Matrix

| Context | Tool | What It Measures | Threshold | Blocks? | Trigger | File |
|---------|------|-----------------|-----------|---------|---------|------|
| Pre-prod (CI) | DeepEval (pytest) | Faithfulness, citation accuracy, answer relevancy, context precision | Faithfulness ≥ 0.85, Citation ≥ 0.80, Relevancy ≥ 0.70, Precision ≥ 0.60 | Yes | PR / push | `apps/backend/tests/test_rag_evaluation.py` |
| Pre-prod (CI) | HR regression tests | 14 critical retrieval cases (Code Blue, DNR, meds, etc.) | 14/14 pass | Yes | PR / push | `apps/backend/scripts/verify_hr_retrieval_regressions.py` |
| Pre-prod (CI) | Unit/integration tests | Query validation, synonyms, chat service, safety | All pass | Yes | PR / push | 8 test files in `ci.yml` |
| Monthly gate | PromptFoo | Pass rate, safety flag rate, citation coverage | Pass ≥ 90%, Safety ≤ 5%, Citations ≥ 90% | Yes | Monthly release | `tests/promptfoo/promptfooconfig.yaml` |
| Monthly gate | Baseline comparison | Cross-release regression (pass rate, safety, found rate, latency) | -5pp pass, +3pp safety, -5pp found, +25% latency | Yes | Monthly release | `scripts/persist_evaluation_baseline.py` |
| Monthly gate | Ingestion audit | Schema validation, embedding dimensions, field completeness | All valid | Yes | Monthly release | `apps/backend/scripts/audit_ingestion_quality.py` |
| Weekly | DeepEval (sampled) | Production query quality (50 stratified samples) | Report only (warn < 0.80 faithfulness) | No (email) | Scheduled cron | `scripts/weekly_eval.py` |
| Weekly | PromptFoo (100 cases) | Compliance snapshot | 80% warn | No (warn) | Scheduled cron | `.github/workflows/rag-evaluation.yml` |
| Daily | Chat audit service | Every query + response + citations + latency + confidence | N/A (capture all) | No | Every request | `apps/backend/app/services/chat_audit_service.py` |
| Monthly gate | RAGAS v0.4 regression | Golden test set faithfulness, recall, correctness (50 cases) | No metric drops > 5pp vs baseline | Yes | Monthly release | `scripts/run_ragas_regression.py` |
| Weekly | Combined report | Technical quality + executive usage (merged email) | Report only | No (email) | Scheduled cron | `scripts/run_combined_weekly_report.py` |
| On-demand | Pre-deployment audit | Aggregated quality evidence for auditors (10-section HTML+JSON) | N/A (report) | No | Manual | `scripts/generate_pre_deployment_audit.py` |
| On-demand | Drift detection | Month-over-month found rate, latency, safety flags, confidence | 5pp found, 20% latency, 2pp safety | Report | Manual | `scripts/audit_drift_report.py` |
| Post-index | Post-index test | Retrieval accuracy spot-check after full ingestion | 80% pass | Yes (pipeline) | After ingestion | `tests/rag_accuracy/run_post_index_test.py` |

---

## Framework Inventory

### DeepEval (CI Gate + Weekly Monitoring)

Primary evaluation framework, wired into both CI/CD and weekly production monitoring.

| Metric | Threshold | Weight | Purpose |
|--------|-----------|--------|---------|
| Faithfulness | ≥ 0.85 | 30% | No hallucinations — claims supported by context |
| Policy Citation | ≥ 0.80 | 25% | All claims attributed to policy names/numbers |
| Answer Relevancy | ≥ 0.70 | 20% | Response addresses the question |
| Context Precision | ≥ 0.60 | 15% | Relevant chunks ranked higher |
| Procedural Completeness | ≥ 0.75 | 10% | All procedural steps included |
| Context Recall | ≥ 0.70 | -- | When expected output is available |

**Configuration**: `apps/backend/app/evaluation/metrics.py:74-94`
**Claim-level diagnostics**: `apps/backend/app/evaluation/diagnostics.py` (RAGChecker-style claim decomposition)

### PromptFoo (Monthly Release Gate)

100-case compliance suite enforced during the monthly release pipeline.

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| Pass rate | ≥ 90% | Overall test success |
| Safety flag rate | ≤ 5% | LOW_GROUNDING, BLOCKED, etc. |
| Citation coverage | ≥ 90% | Responses include source citations |

**Configuration**: `tests/promptfoo/promptfooconfig.yaml`

### RAGAS v0.4 (Monthly Regression Testing)

Independent regression framework using a curated 50-question golden test set. Proves document updates don't break retrieval quality by comparing before/after metrics.

| Metric | Max Allowed Drop | Severity |
|--------|-----------------|----------|
| Faithfulness | > 5pp | FAIL |
| LLM Context Recall | > 5pp | FAIL |
| Factual Correctness | > 5pp WARN, > 10pp FAIL | WARN/FAIL |
| Response Relevancy | > 5pp | FAIL |

**Why RAGAS + DeepEval?** DeepEval monitors ongoing quality (production samples, absolute thresholds). RAGAS tests regression on a fixed dataset (delta comparison). Two independent frameworks = defense-in-depth.

**Golden test set**: `data/golden_test_set.json` (50 curated cases across 9 categories)
**Script**: `scripts/run_ragas_regression.py`
**Baseline**: `reports/ragas/baseline.json`

### Chat Audit Service (Continuous Observability)

Non-blocking audit logging captures every interaction for downstream analysis.

| What | How | Storage |
|------|-----|---------|
| Every query + response | Fire-and-forget `asyncio.create_task()` | Azure Blob `chat-audit/YYYY/MM/DD.jsonl` |
| Citations, confidence, latency | Structured `ChatAuditRecord` schema | 90-day retention |
| Safety flags, needs-review markers | Automatic flagging | Feeds drift detection + weekly eval |

**Configuration**: `apps/backend/app/services/chat_audit_service.py`
**Admin API**: `/admin/audit/dates`, `/admin/audit/records/{date}`, `/admin/audit/stats/{date}`

---

## Feedback Loop

```
Collect (every request)  -->  Analyze (weekly eval + reports)
        ^                              |
        |                              v
  Gate (monthly release)  <--  Detect (drift + baselines)
```

1. **Collect**: Chat audit service captures every interaction (query, response, citations, confidence, latency, safety flags)
2. **Analyze**: Weekly DeepEval evaluation on stratified production sample; executive reports classify question categories
3. **Detect**: Monthly drift report compares found rate, latency, safety flags, confidence distribution month-over-month
4. **Gate**: Monthly release pipeline blocks deployment if quality regresses beyond thresholds

Signals that drive improvements:

| Signal | Source | Action |
|--------|--------|--------|
| `needs_human_review` records | Audit logs | Added to test dataset for regression coverage |
| Safety flag patterns | Audit logs | Inform prompt engineering updates |
| Not-found queries | Audit logs | Identify synonym gaps and chunking issues |
| Baseline degradation | Release gate | Block release until root cause addressed |

---

## Quick Reference

**"Is the system tested before deployment?"** Yes — every PR runs DeepEval (6 metrics), HR regression (14 cases), and 8 test suites. Monthly releases add PromptFoo (100 cases), RAGAS regression (50 golden cases), ingestion audit, and baseline comparison. A comprehensive pre-deployment audit report aggregates all evidence.

**"Is production quality monitored?"** Yes — every request is audit-logged. Weekly combined reports merge technical quality metrics with executive usage analytics. Monthly drift reports compare operational metrics.

**"What blocks a bad release?"** 6 quality gates in the monthly pipeline: ingestion audit, HR regression, targeted tests, PromptFoo compliance, RAGAS regression, and cross-release baseline comparison. Any gate failure blocks deployment.

**"Where are the logs?"** Azure Blob Storage `chat-audit/` container, organized by `YYYY/MM/DD.jsonl`. 90-day retention enforced by `scripts/audit_lifecycle_cleanup.py`.
