# Azure Deployment Status - RUSH Policy RAG Agent

> **Last Updated**: December 2, 2025
> **Deployed By**: Juan_Rojas@rush.edu
> **Status**: ✅ PRODUCTION READY

---

## Live URLs

| Component | URL | Status |
|-----------|-----|--------|
| **Frontend (UI)** | https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/ | ✅ Running |
| **Backend (API)** | https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/ | ✅ Running |
| **API Docs (Swagger)** | https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/docs | ✅ Available |
| **Health Check** | https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health | ✅ Healthy |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AZURE CONTAINER APPS (East US)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────┐     ┌─────────────────────────────┐        │
│  │   rush-policy-frontend      │     │   rush-policy-backend       │        │
│  │   ─────────────────────     │     │   ─────────────────────     │        │
│  │   Next.js 16 (Node.js)      │────▶│   FastAPI 0.115 (Python)    │        │
│  │   Port: 3000                │     │   Port: 8000                │        │
│  │   CPU: 0.5 | RAM: 1Gi       │     │   CPU: 1.0 | RAM: 2Gi       │        │
│  │   Replicas: 1-5             │     │   Replicas: 1-5             │        │
│  └─────────────────────────────┘     └──────────────┬──────────────┘        │
│                                                      │                       │
│  Environment: rush-policy-env-production             │                       │
└──────────────────────────────────────────────────────┼───────────────────────┘
                                                       │
                        ┌──────────────────────────────┼───────────────────────┐
                        │                              ▼                       │
                        │  ┌─────────────────────────────────────────────┐    │
                        │  │           AZURE AI SERVICES                 │    │
                        │  ├─────────────────────────────────────────────┤    │
                        │  │                                             │    │
                        │  │  Azure AI Search (policychataisearch)       │    │
                        │  │  ├── Index: rush-policies                   │    │
                        │  │  ├── Documents: 16,980 chunks               │    │
                        │  │  ├── Fields: 36 (incl. vectors)             │    │
                        │  │  └── Query: vectorSemanticHybrid            │    │
                        │  │                                             │    │
                        │  │  Azure OpenAI (rua-nonprod-ai-innovation)   │    │
                        │  │  ├── Chat: gpt-4.1                          │    │
                        │  │  └── Embeddings: text-embedding-3-large     │    │
                        │  │                                             │    │
                        │  │  Cohere Rerank 3.5 (Azure AI Foundry)       │    │
                        │  │  ├── Model: cohere-rerank-v3-5              │    │
                        │  │  ├── Top N: 10                              │    │
                        │  │  └── Min Score: 0.25                        │    │
                        │  │                                             │    │
                        │  │  Azure Blob Storage (policytechrush)        │    │
                        │  │  ├── Container: policies-active             │    │
                        │  │  └── CORS: Frontend URL enabled             │    │
                        │  │                                             │    │
                        │  └─────────────────────────────────────────────┘    │
                        │                                                      │
                        │         AZURE SUBSCRIPTION: RU-Azure-NonProd         │
                        │         RESOURCE GROUP: RU-A-NonProd-AI-Innovation-RG│
                        └──────────────────────────────────────────────────────┘
```

---

## Resource Inventory

### Container Apps

| Resource | Name | Location | Status |
|----------|------|----------|--------|
| Environment | `rush-policy-env-production` | East US | ✅ Succeeded |
| Frontend App | `rush-policy-frontend` | East US | ✅ Running |
| Backend App | `rush-policy-backend` | East US | ✅ Running |

### Container Registry

| Property | Value |
|----------|-------|
| Name | `aiinnovation` |
| Login Server | `aiinnovation.azurecr.io` |
| SKU | Basic |
| Images | `policytech-backend:latest`, `policytech-frontend:latest` |

### AI Services

| Service | Endpoint | Configuration |
|---------|----------|---------------|
| Azure AI Search | `policychataisearch.search.windows.net` | Index: `rush-policies`, 16,980 docs |
| Azure OpenAI | `rua-nonprod-ai-innovation.openai.azure.com` | GPT-4.1, text-embedding-3-large |
| Cohere Rerank | `Cohere-rerank-v3-5-beomo.eastus2.models.ai.azure.com` | cohere-rerank-v3-5 |

### Storage

| Property | Value |
|----------|-------|
| Account | `policytechrush` |
| Container | `policies-active` |
| CORS Origins | `localhost:3000`, `localhost:3001`, Frontend URL |

---

## Container Configuration

### Backend (`rush-policy-backend`)

```yaml
image: aiinnovation.azurecr.io/policytech-backend:latest
resources:
  cpu: 1.0
  memory: 2Gi
scaling:
  minReplicas: 1
  maxReplicas: 5
ingress:
  external: true
  targetPort: 8000
environment_variables:
  - SEARCH_ENDPOINT
  - SEARCH_API_KEY
  - AOAI_ENDPOINT
  - AOAI_CHAT_DEPLOYMENT
  - AOAI_EMBEDDING_DEPLOYMENT
  - AOAI_API
  - STORAGE_CONNECTION_STRING
  - CONTAINER_NAME
  - USE_ON_YOUR_DATA
  - USE_COHERE_RERANK
  - COHERE_RERANK_ENDPOINT
  - COHERE_RERANK_API_KEY
  - COHERE_RERANK_MODEL
  - COHERE_RERANK_TOP_N
  - COHERE_RERANK_MIN_SCORE
  - CORS_ORIGINS
```

### Frontend (`rush-policy-frontend`)

```yaml
image: aiinnovation.azurecr.io/policytech-frontend:latest
resources:
  cpu: 0.5
  memory: 1Gi
scaling:
  minReplicas: 1
  maxReplicas: 5
ingress:
  external: true
  targetPort: 3000
environment_variables:
  - BACKEND_URL
  - NEXT_PUBLIC_API_URL
  - NODE_ENV
```

---

## Security Configuration

### Frontend Security Headers

| Header | Value |
|--------|-------|
| Strict-Transport-Security | `max-age=63072000; includeSubDomains; preload` |
| X-Frame-Options | `DENY` |
| X-Content-Type-Options | `nosniff` |
| X-XSS-Protection | `1; mode=block` |
| Referrer-Policy | `strict-origin-when-cross-origin` |
| Permissions-Policy | `camera=(), microphone=(), geolocation=()` |
| Content-Security-Policy | Full CSP with restricted sources |

### Backend CORS

Allowed Origins:
- `https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`
- `http://localhost:3000`
- `http://localhost:5000`

### Blob Storage CORS

Allowed Origins:
- `https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`
- `http://localhost:3000`
- `http://localhost:3001`

---

## Health Check Results

### Backend Health (`/health`)

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
  "circuit_breakers": {
    "azure_search": { "state": "closed", "fail_counter": 0 },
    "azure_openai": { "state": "closed", "fail_counter": 0 }
  },
  "blob_storage": {
    "configured": true,
    "container": "policies-active",
    "accessible": true
  },
  "version": "3.0.0"
}
```

### Frontend Health (`/api/health`)

```json
{
  "status": "ok",
  "timestamp": "2025-12-03T00:27:25.241Z"
}
```

---

## Verified Features

| Feature | Status | Test Result |
|---------|--------|-------------|
| Chat API | ✅ Working | Returns citations with Cohere rerank scores |
| PDF Viewing | ✅ Working | SAS URLs generated, CORS configured |
| Health Endpoints | ✅ Working | Both frontend and backend healthy |
| Security Headers | ✅ Applied | HSTS, CSP, X-Frame-Options all present |
| Cohere Rerank | ✅ Enabled | Cross-encoder reranking active |
| Azure Search | ✅ Connected | 16,980 documents indexed |
| Blob Storage | ✅ Accessible | PDF files available |

---

## Deployment Workflow

### Updating Existing Deployment (Most Common)

Run these commands in order after pushing code changes to git:

```bash
# Step 1: Build backend image in ACR (~15 minutes)
cd apps/backend
az acr build --registry aiinnovation --image policytech-backend:latest .

# Step 2: Build frontend image in ACR (~2-3 minutes)
cd apps/frontend
az acr build --registry aiinnovation --image policytech-frontend:latest .

# Step 3: Update backend container app (~30 seconds)
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest

# Step 4: Update frontend container app (~30 seconds)
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest

# Step 5: Verify deployment
curl https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health
curl https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/api/health
```

### Quick Reference

| Step | Command | Time |
|------|---------|------|
| 1. Build Backend | `az acr build --registry aiinnovation --image policytech-backend:latest .` | ~15 min |
| 2. Build Frontend | `az acr build --registry aiinnovation --image policytech-frontend:latest .` | ~2-3 min |
| 3. Update Backend | `az containerapp update --name rush-policy-backend ...` | ~30 sec |
| 4. Update Frontend | `az containerapp update --name rush-policy-frontend ...` | ~30 sec |
| 5. Verify | `curl .../health` | instant |

### First-Time Setup (New Environment)

If Container Apps don't exist yet, run these steps first:

```bash
# Variables
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG"
ACR_NAME="aiinnovation"
ENV_NAME="rush-policy-env"

# 1. Create Container Apps Environment
az containerapp env create \
  --name $ENV_NAME \
  --resource-group $RESOURCE_GROUP \
  --location eastus

# 2. Build images
az acr build --registry $ACR_NAME --image policytech-backend:latest ./apps/backend
az acr build --registry $ACR_NAME --image policytech-frontend:latest ./apps/frontend

# 3. Get ACR credentials
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# 4. Create backend container app
az containerapp create \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --environment $ENV_NAME \
  --image ${ACR_NAME}.azurecr.io/policytech-backend:latest \
  --registry-server ${ACR_NAME}.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5 \
  --cpu 1.0 \
  --memory 2Gi \
  --env-vars \
    SEARCH_ENDPOINT="https://policychataisearch.search.windows.net" \
    SEARCH_API_KEY="$SEARCH_API_KEY" \
    SEARCH_INDEX_NAME="rush-policies" \
    SEARCH_SEMANTIC_CONFIG="my-semantic-config" \
    AOAI_ENDPOINT="https://ai-aihubnonprod680009095162.openai.azure.com/" \
    AOAI_API_KEY="$AOAI_API_KEY" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    STORAGE_CONNECTION_STRING="$STORAGE_CONNECTION_STRING" \
    CONTAINER_NAME="policies-active" \
    SOURCE_CONTAINER_NAME="policies-source" \
    USE_ON_YOUR_DATA="true" \
    USE_COHERE_RERANK="true" \
    COHERE_RERANK_ENDPOINT="$COHERE_RERANK_ENDPOINT" \
    COHERE_RERANK_API_KEY="$COHERE_RERANK_API_KEY" \
    COHERE_RERANK_MODEL="cohere-rerank-v3-5" \
    COHERE_RERANK_TOP_N="10" \
    COHERE_RERANK_MIN_SCORE="0.25" \
    BACKEND_PORT="8000" \
    LOG_FORMAT="json" \
    CORS_ORIGINS="http://localhost:3000"

# 5. Get backend URL for frontend config
BACKEND_URL=$(az containerapp show --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# 6. Create frontend container app
az containerapp create \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --environment $ENV_NAME \
  --image ${ACR_NAME}.azurecr.io/policytech-frontend:latest \
  --registry-server ${ACR_NAME}.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASSWORD" \
  --target-port 3000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5 \
  --cpu 0.5 \
  --memory 1Gi \
  --env-vars \
    BACKEND_URL="https://$BACKEND_URL" \
    NODE_ENV="production" \
    PORT="3000"

# 7. Update backend CORS with frontend URL
FRONTEND_URL=$(az containerapp show --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --query "properties.configuration.ingress.fqdn" -o tsv)

az containerapp update \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --set-env-vars CORS_ORIGINS="https://$FRONTEND_URL,http://localhost:3000"

# 8. Verify both apps
curl https://$BACKEND_URL/health
curl https://$FRONTEND_URL/api/health
```

### Monitor ACR Build Progress

```bash
# List recent builds
az acr task list-runs --registry aiinnovation -o table

# View build logs (replace cp4 with run ID)
az acr task logs --registry aiinnovation --run-id cp4
```

---

## Maintenance Commands

### View Logs

```bash
# Backend logs
az containerapp logs show --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG --tail 50

# Frontend logs
az containerapp logs show --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG --tail 50
```

### Restart Apps

```bash
# Restart backend
az containerapp revision restart --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --revision rush-policy-backend--0000001

# Restart frontend
az containerapp revision restart --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --revision rush-policy-frontend--80tbk5p
```

### Update Container Images

```bash
# Rebuild and push backend
cd apps/backend
az acr build --registry aiinnovation --image policytech-backend:latest .

# Update container app to use new image
az containerapp update --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest
```

### Scale Apps

```bash
# Scale backend (min 2, max 10 replicas)
az containerapp update --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --min-replicas 2 --max-replicas 10
```

---

## Cost Optimization Notes

- Container Apps billing is based on vCPU-seconds and memory GB-seconds
- Current config: ~1.5 vCPU, 3 GB RAM total (both apps at min replicas)
- Consider scaling to 0 for non-production environments
- Cohere Rerank billed per 1000 queries (see APP_COST.md)

---

## Troubleshooting

### PDF Not Loading

1. Check blob storage CORS includes frontend URL
2. Verify SAS token generation: `curl https://rush-policy-backend.../api/pdf/filename.pdf`
3. Check browser console for CORS errors

### Chat API Errors

1. Check backend health: `curl https://rush-policy-backend.../health`
2. View backend logs for error details
3. Verify circuit breakers are "closed" (not "open")

### Container Not Starting

1. Check revision health: `az containerapp revision list --name <app-name> -g <rg>`
2. View container logs for startup errors
3. Verify environment variables are set correctly

---

## Related Documentation

- [DEPLOYMENT.md](../DEPLOYMENT.md) - Full deployment guide
- [APP_COST.md](../APP_COST.md) - Cost analysis and projections
- [CLAUDE.md](../CLAUDE.md) - Development guidelines
