# RUSH Policy RAG Agent - Cost Documentation

> **Budget Planning Reference**
>
> | Tier | Monthly Cost | Annual (with 10% buffer) |
> |------|-------------|--------------------------|
> | **Pilot** (30-50 queries/day) | $136-$236 | **$2,900** |
> | **Low** (50 queries/day) | $149-$249 | **$3,300** |
> | **Medium** (100 queries/day) | $207-$307 | **$4,100** |
> | **High** (250 queries/day) | $362-$462 | **$5,100** |
>
> *Costs include infrastructure only. Add $100/mo for Azure Support (optional).*

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PRODUCTION STACK (FastAPI + Next.js)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Browser → Next.js Frontend → FastAPI Backend → Azure Services              │
│                                                    ├── Azure AI Search       │
│                                                    ├── Azure OpenAI          │
│                                                    └── Azure Blob Storage    │
│                                                                              │
│  NO AZURE FUNCTIONS - Pure FastAPI + Next.js                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Tech Stack**: FastAPI backend + Next.js 16 frontend + Azure OpenAI "On Your Data" (vectorSemanticHybrid)

**Architecture Status**: ✅ COMPLETED - See [CLAUDE.md](CLAUDE.md) for architecture details

---

## Required Azure Resources

> ⚠️ **Important**: This application **requires** the following Azure services to function:

| Azure Service | Purpose | Required | Monthly Cost |
|---------------|---------|----------|--------------|
| **Azure AI Search** | Vector store (3072-dim) + 132 synonym rules | ✅ **Required** | ~$75-$80 (Basic) |
| **Azure OpenAI** | GPT-4.1 + text-embedding-3-large | ✅ **Required** | $10-$60 (varies by query volume) |
| **Azure AI Foundry** | Cohere Rerank 4.0 Pro (Serverless) | ✅ **Required** | $2.60-$15 |
| **Azure Blob Storage** | PDF storage (3 containers) | ✅ **Required** | $1.40-$10 |
| **Azure Container Apps** | Host backend + frontend | ✅ **Required** | $45-$190 |
| **Azure Container Registry** | Container images | ✅ **Required** | ~$5 (Basic) |
| **Application Insights** | Monitoring & logging | Optional | $0-$7 |

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Usage Tier Definitions](#usage-tier-definitions)
3. [Cost Components](#cost-components)
4. [Total Monthly Costs](#total-monthly-costs)
5. [One-Time Setup Costs](#one-time-setup-costs)
6. [Support & Maintenance](#support--maintenance)
7. [Annual Budget Projections](#annual-budget-projections)
8. [Cost Optimization](#cost-optimization)
9. [Pricing Sources](#pricing-sources)

---

## Executive Summary

The RUSH Policy RAG Agent is a production-ready application using **FastAPI + Next.js + Azure OpenAI "On Your Data"** for intelligent policy retrieval. The architecture has been simplified to eliminate Azure Functions - all traffic flows through FastAPI.

Monthly operational costs scale primarily with query volume and compute requirements.

### Cost Drivers (by impact)

| Component | % of Total | Scales With |
|-----------|-----------|-------------|
| **Azure AI Search** | 50-60% | Fixed (tier-based) - dominant at lower volumes |
| **Container Apps** | 30-50% | Traffic & replicas |
| **Azure OpenAI** | 8-17% | Query volume (tokens) - significantly reduced with corrected pricing |
| **Storage & Monitoring** | 1-5% | Document count & logs |

### Key Assumptions

- **Employee base**: 15,000 employees, ~5,000 potential daily users
- **Initial pilot**: Few hundred queries per week (~1,300/month)
- **Max capacity**: 250 queries/day (~7,500/month) for high-tier production
- **Average query**: 2,000 input tokens, 500 output tokens
- **Document corpus**: ~1,800 PDFs, ~10,000 chunks
- **Search tier**: Basic ($75/mo) - vector + keyword search, no semantic ranking
- **Compute**: Azure Container Apps with autoscaling

---

## Usage Tier Definitions

Based on RUSH employee base of **15,000 employees** with **~5,000 potential daily users**:

| Tier | Queries/Day | Queries/Month | Est. Users | Use Case |
|------|-------------|---------------|------------|----------|
| **Pilot** | 30-50 | 1,300 | ~50-100 | Initial pilot phase (few hundred queries/week) |
| **Low** | 50 | 1,500 | ~100-200 | Limited department rollout |
| **Medium** | 100 | 3,000 | ~300-500 | Department-wide adoption |
| **High** | 250 | 7,500 | ~500-1,000 | Full production capacity |

---

## Cost Components

### A. Azure OpenAI (Variable - Token-Based)

The largest variable cost component, scaling directly with query volume.

**Azure OpenAI "On Your Data"** (vectorSemanticHybrid search)
- Uses Chat Completions API with integrated Azure AI Search
- Combines Vector + BM25 + L2 Semantic Reranking for best quality
- No additional cost beyond standard token pricing

**GPT-4.1 Chat Completions**
- Input tokens: $2.00 per 1,000,000 tokens ($0.002 per 1,000 tokens)
- Output tokens: $8.00 per 1,000,000 tokens ($0.008 per 1,000 tokens)
- Average query: ~2,000 input tokens (query + RAG context)
- Average response: ~500 output tokens
- **Per-query cost**: ~$0.008

**text-embedding-3-large (3072 dimensions)**
- Rate: ~$0.13 per 1,000,000 tokens
- Query embedding: ~20 tokens = $0.0000026 per query
- *Note: Negligible compared to chat completions*

| Tier | Queries | Chat Cost | Embedding Cost | **Total** | **95% CI** |
|------|---------|-----------|----------------|-----------|------------|
| Pilot | 1,300 | $10.40 | $0.03 | **$10.43** | $8-$14 |
| Low | 1,500 | $12.00 | $0.04 | **$12.04** | $10-$16 |
| Medium | 3,000 | $24.00 | $0.08 | **$24.08** | $20-$30 |
| High | 7,500 | $60.00 | $0.20 | **$60.20** | $50-$75 |

*CI reflects variation in query complexity (simple lookups vs. complex multi-policy questions)*

---

### B. Azure AI Search (Fixed + Minimal Variable)

Fixed monthly cost based on tier selection.

**Basic Tier** (~$75/month)
- Vector search (HNSW algorithm, 3072-dim embeddings)
- BM25 keyword search
- Synonym maps (132 healthcare rules)
- 15 GB storage, 3 replicas max
- **L2 Semantic Ranking**: Included via Azure OpenAI "On Your Data" integration

> **Note**: Semantic ranking is achieved through the vectorSemanticHybrid query type in Azure OpenAI "On Your Data", which provides L2 reranking without requiring Standard S1 tier.

| Tier | Base Cost | Query Overhead | **Total** | **95% CI** |
|------|-----------|----------------|-----------|------------|
| Pilot | $75 | ~$2 | **$77** | $75-$82 |
| Low | $75 | ~$2 | **$77** | $75-$82 |
| Medium | $75 | ~$3 | **$78** | $75-$85 |
| High | $75 | ~$5 | **$80** | $75-$88 |

*Query overhead includes additional storage units consumed by query logs*

---

### C. Cohere Rerank (Serverless - Azure AI Foundry)

Cross-encoder reranking improves precision by re-scoring the top 50 documents.
Pricing is based on "Search Units" (1 unit = 1 query with up to 100 documents).

**Cohere Rerank 4.0 Pro** (Dec 2025)
- Price: $2.00 per 1,000 queries (Search Units)
- Reranking 50 documents per query counts as 1 unit.
- 9.5% accuracy improvement over v3.5, 32k context window, healthcare-optimized.

| Tier | Queries | Unit Cost | **Total** | **95% CI** |
|------|---------|-----------|-----------|------------|
| Pilot | 1,300 | $0.002 | **$2.60** | $2-$3 |
| Low | 1,500 | $0.002 | **$3.00** | $2.50-$3.50 |
| Medium | 3,000 | $0.002 | **$6.00** | $5-$7 |
| High | 7,500 | $0.002 | **$15.00** | $13-$17 |

---

### D. Azure Container Apps (Compute)

Consumption-based pricing with free tier allowance.

**Backend Service** (FastAPI)
- Resources: 1 vCPU, 2 GiB memory
- Production: 2 replicas minimum (HA)
- Rate: ~$0.000024/sec vCPU + $0.000003/sec GiB

**Frontend Service** (Next.js)
- Resources: 0.5 vCPU, 1 GiB memory
- Production: 2 replicas minimum (HA)

**Free Tier Allowance** (per subscription/month)
- 180,000 vCPU-seconds
- 360,000 GiB-seconds

| Tier | Backend | Frontend | **Total** | **95% CI** |
|------|---------|----------|-----------|------------|
| Pilot | $30 | $15 | **$45** | $30-$60 |
| Low | $35 | $20 | **$55** | $40-$75 |
| Medium | $60 | $35 | **$95** | $70-$130 |
| High | $120 | $70 | **$190** | $140-$260 |

*CI reflects scaling behavior during traffic spikes*

---

### E. Azure Blob Storage

Minimal cost for document storage. **Required for PDF viewing feature.**

**3-Container Architecture**:
- `policies-source` - Staging area (upload new PDFs here)
- `policies-active` - Production (auto-synced, serves PDF viewer)
- `policies-archive` - Deleted policies (retention)

**Capacity**: ~5 GB (1,800 PDFs)
- Hot tier: $0.018/GB/month = ~$0.10/month

**Operations**:
- Read transactions: $0.0055 per 10,000 operations
- Data egress (PDF downloads): $0.087/GB

| Tier | Storage | Operations | Egress | **Total** | **95% CI** |
|------|---------|------------|--------|-----------|------------|
| Pilot | $0.10 | $0.30 | $1 | **$1.40** | $1-$2 |
| Low | $0.10 | $0.40 | $1.50 | **$2.00** | $1.50-$3 |
| Medium | $0.10 | $0.80 | $3 | **$3.90** | $3-$6 |
| High | $0.10 | $2 | $8 | **$10.10** | $7-$15 |

---

### F. Monitoring (Application Insights + Log Analytics)

**Data Ingestion**: ~$2.30/GB
**Free Tier**: First 5 GB/month included

| Tier | Est. Data Volume | **Total** | **95% CI** |
|------|------------------|-----------|------------|
| Pilot | ~1 GB | **$0** | $0-$2 |
| Low | ~1.5 GB | **$0** | $0-$3 |
| Medium | ~3 GB | **$0** | $0-$5 |
| High | ~8 GB | **$7** | $5-$12 |

---

## Total Monthly Costs

### Infrastructure Only

| Tier | OpenAI | Search | Rerank | Compute | Storage | Monitor | **TOTAL** | **95% CI** |
|------|--------|--------|--------|---------|---------|---------|-----------|------------|
| **Pilot** | $10.43 | $77 | $2.60 | $45 | $1.40 | $0 | **$136** | **$115-$165** |
| **Low** | $12.04 | $77 | $3.00 | $55 | $2.00 | $0 | **$149** | **$125-$180** |
| **Medium** | $24.08 | $78 | $6.00 | $95 | $3.90 | $0 | **$207** | **$175-$250** |
| **High** | $60.20 | $80 | $15.00 | $190 | $10.10 | $7 | **$362** | **$310-$430** |

### Cost Distribution by Tier

```
Pilot Tier ($136/mo)             Low Tier ($149/mo)              Medium Tier ($207/mo)         High Tier ($362/mo)
┌────────────────────┐            ┌────────────────────┐          ┌────────────────────┐        ┌────────────────────┐
│ Search      57%    │            │ Search      52%    │          │ Search      38%    │        │ Compute     52%    │
│ Compute     33%    │            │ Compute     37%    │          │ Compute     46%    │        │ Search      22%    │
│ OpenAI       8%    │            │ OpenAI       8%    │          │ OpenAI      12%    │        │ OpenAI      17%    │
│ Rerank       2%    │            │ Rerank       2%    │          │ Rerank       3%    │        │ Storage      3%    │
│ Storage      1%    │            │ Storage      1%    │          │ Storage      2%    │        │ Monitor      2%    │
└────────────────────┘            └────────────────────┘          └────────────────────┘        └────────────────────┘
```

---

## One-Time Setup Costs

| Item | Cost | Notes |
|------|------|-------|
| Initial PDF ingestion | ~$5 | Embeddings for ~10,000 chunks |
| Search index creation | $0 | Included in Search tier |
| Azure OpenAI deployment | $0 | Model deployments included |
| Azure AI Foundry | $0 | Serverless model deployment |
| Container registry setup | $0 | Basic tier (5 GB free) |
| Blob container setup | $0 | 3 containers (source, active, archive) |
| **Total Setup** | **~$5** | One-time only |

---

## Support & Maintenance

### Azure Support Plans (Optional)

| Plan | Monthly Cost | Response Time | Recommendation |
|------|-------------|---------------|----------------|
| Basic | $0 | Web only | Development/testing |
| Developer | $29 | 8 hours (business) | Staging environments |
| **Standard** | **$100** | **1 hour (critical)** | **Production (recommended)** |
| Professional Direct | $1,000 | 15 min (critical) | Mission-critical only |

### Engineering Maintenance

| Activity | Frequency | Est. Hours | Notes |
|----------|-----------|------------|-------|
| Health monitoring | Weekly | 1-2 hrs | Dashboard review, alerts |
| Security patches | Monthly | 2-4 hrs | Container rebuilds |
| Index re-sync | As needed | 1 hr | When policies change |
| Performance tuning | Quarterly | 4-8 hrs | Query optimization |
| **Total Monthly** | - | **4-8 hrs** | - |

### Operational Costs

| Item | Monthly Cost | Notes |
|------|-------------|-------|
| Document re-indexing | ~$1 | Re-embedding changed policies |
| Log retention (90 days) | Included | Application Insights default |
| Backup & disaster recovery | Included | Azure native replication |

---

## Annual Budget Projections

### Infrastructure + Support (Standard Plan)

| Tier | Monthly Infra | + Support | **Monthly Total** | **Annual** | **+10% Buffer** |
|------|---------------|-----------|-------------------|------------|-----------------|
| **Pilot** | $136 | $100 | **$236** | $2,832 | **$3,100** |
| **Low** | $149 | $100 | **$249** | $2,988 | **$3,300** |
| **Medium** | $207 | $100 | **$307** | $3,684 | **$4,100** |
| **High** | $362 | $100 | **$462** | $5,544 | **$6,100** |

### Infrastructure Only (No Azure Support)

| Tier | Monthly | Annual | +10% Buffer |
|------|---------|--------|-------------|
| **Pilot** | $136 | $1,632 | **$1,800** |
| **Low** | $149 | $1,788 | **$2,000** |
| **Medium** | $207 | $2,484 | **$2,700** |
| **High** | $362 | $4,344 | **$4,800** |

---

## Cost Optimization

### Quick Wins

1. **Scale to Zero** (Save 30-50% on compute)
   - Configure Container Apps to scale to 0 during off-hours (nights/weekends)
   - Est. savings: $15-$95/month (varies by tier)

2. **Response Caching** (Save 10-20% on OpenAI)
   - Cache frequent queries (e.g., "verbal orders policy")
   - Est. savings: $1-$12/month (OpenAI costs are now much lower)

3. **Reserved Capacity** (Save 20-30% on Search)
   - 1-year commitment on Azure AI Search
   - Est. savings: $15-$24/month (largest fixed cost component)

### Medium-Term Optimizations

4. **Query Optimization**
   - Reduce RAG context window for simple queries
   - Use GPT-4.1-mini for classification tasks

5. **Tiered Storage**
   - Move archived policies to Cool tier ($0.01/GB)
   - Enable lifecycle management

### Cost Monitoring

- Set up **Azure Cost Alerts** at 80% and 100% of budget
- Enable **Cost Analysis** by resource tag
- Review **Advisor Recommendations** monthly

---

## Pricing Sources

All pricing based on Azure East US region as of **February 2026**:

- [Azure OpenAI Pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/)
- [Azure AI Search Pricing](https://azure.microsoft.com/en-us/pricing/details/search/)
- [Azure Container Apps Pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [Azure Blob Storage Pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
- [Azure Monitor Pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/)
- [Azure Support Plans](https://azure.microsoft.com/en-us/support/plans/)

### Pricing Research Notes

**Research Date**: February 2026

**Key Pricing Updates**:
- **GPT-4.1**: Verified pricing at $2.00 per 1M input tokens and $8.00 per 1M output tokens (significantly lower than previous estimates)
- **Azure AI Search Basic**: Confirmed at ~$75/month (fixed cost)
- **Cohere Rerank 4.0 Pro**: Verified at $2.00 per 1,000 queries (Search Units)
- **Azure Container Apps**: Consumption pricing verified with free tier allowances
- **Azure Blob Storage**: Hot tier pricing confirmed at $0.018/GB/month

**Regional Variations**: All pricing reflects Azure East US region. Costs may vary by region and currency exchange rates. Enterprise agreements may offer volume discounts.

**Pricing Verification**: Pricing verified through Azure official pricing pages and pricing calculators. Actual costs may vary based on:
- Enterprise agreement discounts
- Regional pricing differences
- Currency exchange rates
- Volume commitments

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-03 | 2.0 | **Major pricing correction**: Updated GPT-4.1 pricing ($2/1M input, $8/1M output), added Pilot tier, adjusted usage tiers to match RUSH employee base (15K employees, ~5K potential users), recalculated all costs - 60-70% reduction in OpenAI costs |
| 2025-12-02 | 1.2 | Added Cohere Rerank costs and updated budget projections |
| 2025-11-28 | 1.1 | Updated for simplified architecture (FastAPI + Next.js only, no Azure Functions) |
| 2025-11-26 | 1.0 | Initial cost documentation |

---

*Last updated: February 2026*
