# RUSH Policy RAG Agent - Cost Documentation

> **Budget Planning Reference**
>
> | Tier | Monthly Cost | Annual (with 10% buffer) |
> |------|-------------|--------------------------|
> | **Low** (100 queries/day) | $256-$356 | **$5,800** |
> | **Medium** (250 queries/day) | $456-$556 | **$8,500** |
> | **High** (500 queries/day) | $777-$877 | **$12,850** |
>
> *Costs include infrastructure only. Add $100/mo for Azure Support (optional).*

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

The RUSH Policy RAG Agent is a full-stack application leveraging Azure AI services for intelligent policy retrieval. Monthly operational costs scale primarily with query volume and compute requirements.

### Cost Drivers (by impact)

| Component | % of Total | Scales With |
|-----------|-----------|-------------|
| **Azure OpenAI** | 40-55% | Query volume (tokens) |
| **Azure AI Search** | 15-30% | Fixed (tier-based) |
| **Container Apps** | 20-35% | Traffic & replicas |
| **Storage & Monitoring** | 2-8% | Document count & logs |

### Key Assumptions

- **Max capacity**: 500 queries/day (~15,000/month)
- **Average query**: 2,000 input tokens, 500 output tokens
- **Document corpus**: ~1,800 PDFs, ~10,000 chunks
- **Search tier**: Basic ($75/mo) - vector + keyword search, no semantic ranking
- **Compute**: Azure Container Apps with autoscaling

---

## Usage Tier Definitions

Based on maximum traffic capacity of **500 queries/day**:

| Tier | Queries/Day | Queries/Month | Est. Users | Use Case |
|------|-------------|---------------|------------|----------|
| **Low** | 100 | 3,000 | ~100 | Pilot phase, limited rollout |
| **Medium** | 250 | 7,500 | ~300 | Department-wide adoption |
| **High** | 500 | 15,000 | ~500 | Full capacity (max traffic) |

---

## Cost Components

### A. Azure OpenAI (Variable - Token-Based)

The largest variable cost component, scaling directly with query volume.

**GPT-4.1 Chat Completions**
- Input tokens: ~$0.01 per 1,000 tokens
- Output tokens: ~$0.03 per 1,000 tokens
- Average query: ~2,000 input tokens (query + RAG context)
- Average response: ~500 output tokens
- **Per-query cost**: ~$0.035

**text-embedding-3-large (3072 dimensions)**
- Rate: ~$0.13 per 1,000,000 tokens
- Query embedding: ~20 tokens = $0.0000026 per query
- *Note: Negligible compared to chat completions*

| Tier | Queries | Chat Cost | Embedding Cost | **Total** | **95% CI** |
|------|---------|-----------|----------------|-----------|------------|
| Low | 3,000 | $105 | $0.08 | **$105** | $85-$135 |
| Medium | 7,500 | $263 | $0.20 | **$263** | $210-$340 |
| High | 15,000 | $525 | $0.39 | **$525** | $420-$680 |

*CI reflects variation in query complexity (simple lookups vs. complex multi-policy questions)*

---

### B. Azure AI Search (Fixed + Minimal Variable)

Fixed monthly cost based on tier selection.

**Basic Tier** (~$75/month)
- Vector search (HNSW algorithm)
- BM25 keyword search
- Synonym maps (132 healthcare rules)
- 15 GB storage, 3 replicas max
- **Not included**: Semantic ranking (requires Standard S1 at ~$250/mo)

| Tier | Base Cost | Query Overhead | **Total** | **95% CI** |
|------|-----------|----------------|-----------|------------|
| Low | $75 | ~$3 | **$78** | $75-$85 |
| Medium | $75 | ~$5 | **$80** | $75-$90 |
| High | $75 | ~$10 | **$85** | $80-$100 |

*Query overhead includes additional storage units consumed by query logs*

---

### C. Azure Container Apps (Compute)

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
| Low | $45 | $25 | **$70** | $50-$100 |
| Medium | $90 | $50 | **$140** | $100-$200 |
| High | $180 | $100 | **$280** | $200-$400 |

*CI reflects scaling behavior during traffic spikes*

---

### D. Azure Blob Storage

Minimal cost for document storage.

**Capacity**: ~5 GB (1,800 PDFs)
- Hot tier: $0.018/GB/month = ~$0.10/month

**Operations**:
- Read transactions: $0.0055 per 10,000 operations
- Data egress (PDF downloads): $0.087/GB

| Tier | Storage | Operations | Egress | **Total** | **95% CI** |
|------|---------|------------|--------|-----------|------------|
| Low | $0.10 | $0.50 | $2 | **$3** | $2-$5 |
| Medium | $0.10 | $2 | $10 | **$12** | $8-$18 |
| High | $0.10 | $8 | $40 | **$48** | $35-$70 |

---

### E. Monitoring (Application Insights + Log Analytics)

**Data Ingestion**: ~$2.30/GB
**Free Tier**: First 5 GB/month included

| Tier | Est. Data Volume | **Total** | **95% CI** |
|------|------------------|-----------|------------|
| Low | ~2 GB | **$0** | $0-$5 |
| Medium | ~8 GB | **$7** | $5-$15 |
| High | ~25 GB | **$46** | $35-$65 |

---

## Total Monthly Costs

### Infrastructure Only

| Tier | OpenAI | Search | Compute | Storage | Monitor | **TOTAL** | **95% CI** |
|------|--------|--------|---------|---------|---------|-----------|------------|
| **Low** | $105 | $78 | $70 | $3 | $0 | **$256** | **$220-$320** |
| **Medium** | $263 | $80 | $140 | $12 | $7 | **$502** | **$410-$630** |
| **High** | $525 | $85 | $280 | $48 | $46 | **$984** | **$820-$1,215** |

### Cost Distribution by Tier

```
Low Tier ($256/mo)                Medium Tier ($502/mo)           High Tier ($984/mo)
┌────────────────────┐            ┌────────────────────┐          ┌────────────────────┐
│ OpenAI      41%    │            │ OpenAI      52%    │          │ OpenAI      53%    │
│ Search      30%    │            │ Search      16%    │          │ Compute     28%    │
│ Compute     27%    │            │ Compute     28%    │          │ Search       9%    │
│ Storage      1%    │            │ Storage      2%    │          │ Storage      5%    │
│ Monitor      0%    │            │ Monitor      1%    │          │ Monitor      5%    │
└────────────────────┘            └────────────────────┘          └────────────────────┘
```

---

## One-Time Setup Costs

| Item | Cost | Notes |
|------|------|-------|
| Initial PDF ingestion | ~$5 | Embeddings for ~10,000 chunks |
| Search index creation | $0 | Included in Search tier |
| AI Agent creation | $0 | Azure AI Foundry included |
| Container registry setup | $0 | Basic tier (5 GB free) |
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
| **Low** | $256 | $100 | **$356** | $4,272 | **$4,700** |
| **Medium** | $502 | $100 | **$602** | $7,224 | **$8,000** |
| **High** | $984 | $100 | **$1,084** | $13,008 | **$14,300** |

### Infrastructure Only (No Azure Support)

| Tier | Monthly | Annual | +10% Buffer |
|------|---------|--------|-------------|
| **Low** | $256 | $3,072 | **$3,400** |
| **Medium** | $502 | $6,024 | **$6,600** |
| **High** | $984 | $11,808 | **$13,000** |

---

## Cost Optimization

### Quick Wins

1. **Scale to Zero** (Save 30-50% on compute)
   - Configure Container Apps to scale to 0 during off-hours (nights/weekends)
   - Est. savings: $30-$100/month

2. **Response Caching** (Save 10-20% on OpenAI)
   - Cache frequent queries (e.g., "verbal orders policy")
   - Est. savings: $20-$80/month

3. **Reserved Capacity** (Save 20-30% on Search)
   - 1-year commitment on Azure AI Search
   - Est. savings: $15-$25/month

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

All pricing based on Azure East US region as of November 2025:

- [Azure OpenAI Pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/)
- [Azure AI Search Pricing](https://azure.microsoft.com/en-us/pricing/details/search/)
- [Azure Container Apps Pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [Azure Blob Storage Pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
- [Azure Monitor Pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/)
- [Azure Support Plans](https://azure.microsoft.com/en-us/support/plans/)

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2025-11-26 | 1.0 | Initial cost documentation |

---

*Last updated: November 2025*
