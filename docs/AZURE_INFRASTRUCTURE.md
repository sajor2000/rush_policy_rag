# Azure Infrastructure Documentation

**Last Updated**: 2026-01-08
**Deployment Model**: Production-Only (No Staging)
**Current Version**: melissa-feedback-v1-hotfix2

---

## Overview

This document provides a comprehensive overview of the Azure infrastructure for the RUSH Policy RAG Agent. The system uses a **production-only deployment model** to minimize costs while maintaining high availability.

---

## Azure Subscription & Resource Group

| Resource | Value |
|----------|-------|
| **Subscription ID** | `e5282183-61c9-4c17-a58a-9442db9594d5` |
| **Resource Group** | `RU-A-NonProd-AI-Innovation-RG` |
| **Location** | East US |
| **Container Registry** | `aiinnovation.azurecr.io` |

---

## Container Apps Environment

### rush-policy-env-production

**Type**: Container Apps Environment
**Location**: East US
**Purpose**: Production environment hosting both frontend and backend containers

**Configuration**:

- **Managed Environment**: Azure-managed (no self-hosted infrastructure)
- **Networking**: Azure-managed networking
- **Log Analytics**: Integrated with Azure Monitor
- **Ingress**: External HTTPS with auto-generated certificates

---

## Container Apps

### 1. rush-policy-backend (FastAPI Backend)

**Container Image**: `aiinnovation.azurecr.io/policytech-backend:melissa-feedback-v1`

**Resources**:

- **CPU**: 1 core
- **Memory**: 2Gi
- **Scaling**: 1-5 replicas (auto-scale based on HTTP requests)

**Current Revision**: `rush-policy-backend--melissa-feedback-v1`

**URLs**:

- **API Base**: <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io>
- **Health**: <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health>
- **API Docs**: <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/docs>

**Environment Variables**:

```bash
# Azure AI Search
SEARCH_ENDPOINT=https://policychataisearch.search.windows.net
SEARCH_API_KEY=<secret>

# Azure OpenAI
AOAI_ENDPOINT=https://<openai>.openai.azure.com/
AOAI_API_KEY=<secret>
AOAI_CHAT_DEPLOYMENT=gpt-4.1
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Cohere Rerank 3.5
USE_COHERE_RERANK=true
COHERE_RERANK_ENDPOINT=https://<cohere>.models.ai.azure.com
COHERE_RERANK_API_KEY=<secret>
COHERE_RERANK_MODEL=cohere-rerank-v3-5
COHERE_RERANK_TOP_N=10
COHERE_RERANK_MIN_SCORE=0.25

# Azure Blob Storage
STORAGE_CONNECTION_STRING=<secret>
CONTAINER_NAME=policies-active

# Feature Flags
USE_ON_YOUR_DATA=true

# Security
CORS_ORIGINS=https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io
FAIL_ON_MISSING_CONFIG=true
```

---

### 2. rush-policy-frontend (Next.js Frontend)

**Container Image**: `aiinnovation.azurecr.io/policytech-frontend:melissa-feedback-v1-hotfix2`

**Resources**:

- **CPU**: 0.5 cores
- **Memory**: 1Gi
- **Scaling**: 1-5 replicas (auto-scale based on HTTP requests)

**Current Revision**: `rush-policy-frontend--hotfix2`

**URLs**:

- **Frontend**: <https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io>

**Environment Variables**:

```bash
BACKEND_URL=https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io
NEXT_PUBLIC_API_URL=https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io
NODE_ENV=production
```

**Authentication**: Enabled (401 responses for unauthenticated requests)

---

## Container Registry (aiinnovation.azurecr.io)

**Repository**: `policytech-backend`
**Current Tags**:

- `melissa-feedback-v1` (production backend)
- `latest` (alias to melissa-feedback-v1)

**Repository**: `policytech-frontend`
**Current Tags**:

- `melissa-feedback-v1-hotfix2` (production frontend)
- `melissa-feedback-v1-hotfix1` (superseded)
- `melissa-feedback-v1` (superseded)
- `latest` (alias to hotfix2)

---

## Revision Management

Azure Container Apps automatically manages revisions:

- **Active Revisions Mode**: Single (only one revision active at a time)
- **Max Inactive Revisions**: 100 (retained for rollback purposes)
- **Traffic Distribution**: 100% to latest revision
- **Old Revisions**: Automatically deactivated (no resource consumption)

**Current Active Revisions**:

1. `rush-policy-backend--melissa-feedback-v1` (100% traffic)
2. `rush-policy-frontend--hotfix2` (100% traffic)

---

## Cost Breakdown (Estimated)

### Container Apps Costs

| Resource | Specs | Est. Monthly Cost |
|----------|-------|------------------|
| **Backend** | 1 CPU, 2Gi RAM, 1-5 replicas | $30-60 |
| **Frontend** | 0.5 CPU, 1Gi RAM, 1-5 replicas | $15-30 |
| **Environment** | Managed environment overhead | $5-10 |

### Azure AI Services (Consumed on-demand)

- **Azure OpenAI**: Pay-per-token (GPT-4.1 + embeddings)
- **Cohere Rerank**: Pay-per-request via Azure AI Foundry
- **Azure AI Search**: Fixed monthly cost (index size + queries)
- **Azure Blob Storage**: Fixed monthly cost (storage + operations)

**Total Estimated Container Apps Cost**: $50-100/month

**Note**: AI services costs vary based on query volume and are billed separately.

---

## Deployment Procedures

### Quick Deployment (Production)

```bash
# Build Backend
az acr build \
  --registry aiinnovation \
  --image policytech-backend:latest \
  --file apps/backend/Dockerfile \
  apps/backend

# Deploy Backend
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest

# Build Frontend
az acr build \
  --registry aiinnovation \
  --image policytech-frontend:latest \
  --file apps/frontend/Dockerfile \
  apps/frontend

# Deploy Frontend
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest
```

### Emergency Rollback

```bash
# Rollback Backend
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:melissa-feedback-v1

# Rollback Frontend
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:melissa-feedback-v1
```

---

## Monitoring & Logging

### Azure Monitor Integration

- **Application Insights**: Enabled for both containers
- **Log Analytics**: Centralized logging for all containers
- **Metrics**: CPU, memory, requests, response times tracked automatically

### Health Checks

**Backend**:

```bash
curl https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health
```

Expected response:

```json
{
  "status": "healthy",
  "search_index": {
    "index_name": "rush-policies",
    "document_count": 16980,
    "fields": 36
  },
  "on_your_data": {
    "configured": true,
    "query_type": "vectorSemanticHybrid",
    "semantic_config": "default-semantic",
    "enabled": true
  },
  "blob_storage": {
    "configured": true,
    "container": "policies-active",
    "accessible": true
  },
  "version": "3.0.0"
}
```

**Frontend**:

```bash
curl -I https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io
```

Expected: HTTP 200 OK (or 401 if authentication enabled)

### Log Viewing

```bash
# Backend logs
az containerapp logs show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --type console \
  --tail 100

# Frontend logs
az containerapp logs show \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --type console \
  --tail 100
```

---

## Security

### Network Security

- **Ingress**: External HTTPS only (HTTP auto-redirects to HTTPS)
- **Certificates**: Auto-managed by Azure
- **CORS**: Configured to allow frontend domain only
- **Firewall**: Azure-managed (no custom firewall rules)

### Authentication

- **Frontend**: Azure AD authentication enabled (returns 401 for unauthenticated requests)
- **Backend**: Protected by frontend proxy (not directly accessible by end users)

### Secrets Management

All sensitive environment variables are stored as Container App secrets and never exposed in logs or outputs.

---

## Disaster Recovery

### Backup Strategy

- **Container Images**: Stored in Azure Container Registry (geo-redundant)
- **Code**: GitHub repository (primary source of truth)
- **Data**: Azure AI Search index (managed backups)
- **PDFs**: Azure Blob Storage (LRS redundancy)

### Recovery Time Objective (RTO)

- **Backend Rollback**: < 5 minutes
- **Frontend Rollback**: < 5 minutes
- **Full Redeploy from GitHub**: < 30 minutes

### Recovery Point Objective (RPO)

- **Code**: Real-time (GitHub)
- **Search Index**: Real-time (Azure AI Search)
- **Blob Storage**: Real-time (Azure Blob)

---

## Production-Only Deployment Model

### Rationale

- **Cost Optimization**: Eliminates staging/test environment costs (~50% savings)
- **Simplicity**: Single deployment pipeline, easier to maintain
- **Low Risk**: System is stable, updates are infrequent

### Testing Strategy

- **Local Development**: All testing performed locally before production deployment
- **Unit Tests**: 71 tests must pass before deployment (100% pass rate required)
- **TypeScript**: 0 errors required for frontend deployment
- **Health Checks**: Automated post-deployment verification

### Future Considerations

If active development resumes or user feedback cycles increase, consider adding a staging environment:

- **Estimated Cost**: Additional $50-100/month
- **Benefit**: Safe testing environment for QA before production
- **Setup Time**: ~1 hour (duplicate Container Apps with `-staging` suffix)

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-08 | melissa-feedback-v1-hotfix2 | Fixed clarification UI bug (Next.js API route) |
| 2026-01-08 | melissa-feedback-v1-hotfix1 | Fixed clarification UI bug (api.ts) - partial fix |
| 2026-01-08 | melissa-feedback-v1 | Deployed all 6 melissa-feedback features |
| 2025-12-03 | initial-deployment | Initial production deployment |

---

## Support & Contacts

- **Documentation**: See [README.md](../README.md) and [DEPLOYMENT.md](../DEPLOYMENT.md)
- **Deployment Guide**: [deployment-completion-summary.md](deployment-completion-summary.md)
- **Changelog**: [CHANGELOG.md](CHANGELOG.md)
- **Azure Portal**: [Azure Container Apps](https://portal.azure.com/#resource/subscriptions/e5282183-61c9-4c17-a58a-9442db9594d5/resourceGroups/RU-A-NonProd-AI-Innovation-RG/overview)

---

**Document Status**: ✅ Current as of 2026-01-08
**Next Review**: After next major deployment or infrastructure changes
