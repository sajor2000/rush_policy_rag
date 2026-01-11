# Azure Container Apps - Environment Variables CLI Guide

> **Quick Reference** for setting environment variables on Azure Container Apps

---

## Common Mistake (The Fix)

```bash
# WRONG - this is for CREATE only
az containerapp update --env-vars ...

# CORRECT - use --set-env-vars for UPDATE
az containerapp update --set-env-vars ...
```

---

## Prerequisites

```bash
# 1. Login to Azure
az login

# 2. Set correct subscription
az account set --subscription "AI Innovation"

# 3. Verify you're on the right subscription
az account show --query name -o tsv
```

---

## Method 1: Update Existing Container App

```bash
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG"

# Update environment variables on existing app
az containerapp update \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --set-env-vars \
    SEARCH_ENDPOINT="https://policychataisearch.search.windows.net" \
    SEARCH_API_KEY="your-api-key" \
    AOAI_ENDPOINT="https://ai-aihubnonprod680009095162.openai.azure.com/" \
    AOAI_API_KEY="your-aoai-key"
```

---

## Method 2: Create New Container App with Env Vars

```bash
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG"
ACR_NAME="aiinnovation"
ENV_NAME="rush-policy-env"

# Get ACR password
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Create with all environment variables
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
  --max-replicas 10 \
  --cpu 1.0 \
  --memory 2Gi \
  --env-vars \
    SEARCH_ENDPOINT="https://policychataisearch.search.windows.net" \
    SEARCH_API_KEY="your-search-key" \
    SEARCH_INDEX_NAME="rush-policies" \
    SEARCH_SEMANTIC_CONFIG="my-semantic-config" \
    AOAI_ENDPOINT="https://ai-aihubnonprod680009095162.openai.azure.com/" \
    AOAI_API_KEY="your-aoai-key" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    STORAGE_CONNECTION_STRING="your-storage-connection-string" \
    CONTAINER_NAME="policies-active" \
    SOURCE_CONTAINER_NAME="policies-source" \
    USE_ON_YOUR_DATA="true" \
    USE_COHERE_RERANK="true" \
    COHERE_RERANK_ENDPOINT="https://cohere-rerank-v3-5-lywvh.eastus.models.ai.azure.com" \
    COHERE_RERANK_API_KEY="your-cohere-key" \
    COHERE_RERANK_MODEL="cohere-rerank-v3-5" \
    COHERE_RERANK_TOP_N="10" \
    COHERE_RERANK_MIN_SCORE="0.25" \
    BACKEND_PORT="8000" \
    LOG_FORMAT="json" \
    CORS_ORIGINS="http://localhost:3000"
```

---

## Method 3: Using Secrets (Recommended for API Keys)

```bash
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG"

# Step 1: Create secrets (names must be lowercase with hyphens)
az containerapp secret set \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --secrets \
    search-api-key="your-actual-search-key" \
    aoai-api-key="your-actual-aoai-key" \
    cohere-api-key="your-actual-cohere-key" \
    storage-conn-str="DefaultEndpointsProtocol=https;AccountName=..."

# Step 2: Link secrets to environment variables
az containerapp update \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --set-env-vars \
    SEARCH_API_KEY="secretref:search-api-key" \
    AOAI_API_KEY="secretref:aoai-api-key" \
    COHERE_RERANK_API_KEY="secretref:cohere-api-key" \
    STORAGE_CONNECTION_STRING="secretref:storage-conn-str"
```

---

## Verify Configuration

### Check Current Environment Variables
```bash
az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query 'properties.template.containers[0].env' \
  -o table
```

### Check Current Secrets
```bash
az containerapp secret list \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  -o table
```

### Check App Status
```bash
az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query '{name:name, status:properties.runningStatus, url:properties.configuration.ingress.fqdn}' \
  -o table
```

### Test Health Endpoint
```bash
curl https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health
```

---

## Required Environment Variables

### Backend (rush-policy-backend)

| Variable | Description | Example |
|----------|-------------|---------|
| `SEARCH_ENDPOINT` | Azure AI Search endpoint | `https://policychataisearch.search.windows.net` |
| `SEARCH_API_KEY` | Azure AI Search admin key | Use secret |
| `SEARCH_INDEX_NAME` | Search index name | `rush-policies` |
| `SEARCH_SEMANTIC_CONFIG` | Semantic config name | `my-semantic-config` |
| `AOAI_ENDPOINT` | Azure OpenAI endpoint | `https://ai-aihubnonprod680009095162.openai.azure.com/` |
| `AOAI_API_KEY` | Azure OpenAI API key | Use secret |
| `AOAI_CHAT_DEPLOYMENT` | Chat model deployment | `gpt-4.1` |
| `AOAI_EMBEDDING_DEPLOYMENT` | Embedding model | `text-embedding-3-large` |
| `STORAGE_CONNECTION_STRING` | Blob storage connection | Use secret |
| `CONTAINER_NAME` | Active policies container | `policies-active` |
| `SOURCE_CONTAINER_NAME` | Source policies container | `policies-source` |
| `USE_ON_YOUR_DATA` | Enable Azure OYD | `true` |
| `USE_COHERE_RERANK` | Enable Cohere reranking | `true` |
| `COHERE_RERANK_ENDPOINT` | Cohere endpoint | `https://cohere-rerank-v3-5-lywvh.eastus.models.ai.azure.com` |
| `COHERE_RERANK_API_KEY` | Cohere API key | Use secret |
| `COHERE_RERANK_MODEL` | Cohere model name | `cohere-rerank-v3-5` |
| `COHERE_RERANK_TOP_N` | Docs after rerank | `10` |
| `COHERE_RERANK_MIN_SCORE` | Min relevance score | `0.25` |
| `BACKEND_PORT` | Server port | `8000` |
| `CORS_ORIGINS` | Allowed origins | `https://rush-policy-frontend...` |

### Frontend (rush-policy-frontend)

| Variable | Description | Example |
|----------|-------------|---------|
| `BACKEND_URL` | Backend API URL | `https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io` |
| `NODE_ENV` | Environment | `production` |
| `PORT` | Server port | `3000` |

---

## Troubleshooting

### Error: "Invalid env var format"

```bash
# WRONG - spaces around equals
--env-vars VAR_NAME = "value"

# WRONG - missing quotes for special characters
--env-vars STORAGE_CONNECTION_STRING=DefaultEndpoints...

# CORRECT
--env-vars VAR_NAME="value"
--env-vars STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
```

### Error: Variables not updating

Make sure you're using `--set-env-vars` not `--env-vars`:
```bash
# For CREATE: use --env-vars
az containerapp create ... --env-vars VAR="value"

# For UPDATE: use --set-env-vars
az containerapp update ... --set-env-vars VAR="value"
```

### Error: Secret not found

Secret names must be **lowercase with hyphens**:
```bash
# WRONG
--secrets SEARCH_API_KEY="value"

# CORRECT
--secrets search-api-key="value"
```

### View Container Logs

```bash
az containerapp logs show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --follow
```

---

## Quick Copy-Paste Commands

### Update Backend CORS After Frontend Deploy
```bash
FRONTEND_URL=$(az containerapp show --name rush-policy-frontend --resource-group RU-A-NonProd-AI-Innovation-RG --query "properties.configuration.ingress.fqdn" -o tsv)

az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --set-env-vars CORS_ORIGINS="https://$FRONTEND_URL"
```

### Restart Container App
```bash
az containerapp revision restart \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --revision $(az containerapp revision list --name rush-policy-backend --resource-group RU-A-NonProd-AI-Innovation-RG --query "[0].name" -o tsv)
```

---

## Contact

For issues with this deployment, contact the AI Innovation team.
