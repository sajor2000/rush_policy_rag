# Azure Deployment Guide - RUSH Policy RAG Agent

## For the Deployment Team

This guide provides **exact commands** to deploy the RUSH Policy RAG Agent to Azure. Follow the steps in order.

---

## TL;DR - What You're Deploying

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   User Browser  │────▶│  Next.js        │────▶│  FastAPI        │
│                 │     │  Frontend       │     │  Backend        │
│                 │     │  Port 3000      │     │  Port 8000      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │                       │
                              │                       ▼
                              │           ┌───────────────────────┐
                              │           │  Azure AI Search      │
                              │           │  Azure OpenAI         │
                              │           │  Cohere Rerank 3.5    │
                              │           │  Azure Blob Storage   │
                              │           └───────────────────────┘
                              │
                              ▼
                        CONTAINER APPS
```

**Two containers only:**
1. `rush-policy-backend` (FastAPI Python)
2. `rush-policy-frontend` (Next.js Node.js)

**NO Azure Functions. NO Redis. NO Serverless.**

**AI Services:**
- Azure OpenAI (GPT-4.1 + embeddings)
- Cohere Rerank 3.5 (cross-encoder reranking via Azure AI Foundry)

---

## Prerequisites Checklist

Before you start, confirm you have:

- [ ] Azure CLI installed (`az --version` shows 2.50+)
- [ ] Logged into Azure (`az login`)
- [ ] Access to the Rush Azure subscription
- [ ] Git repository cloned to your machine
- [ ] Terminal open in the project root folder

```bash
# Verify Azure CLI
az --version

# Login to Azure
az login

# IMPORTANT: Set the correct subscription (NonProd)
az account set --subscription "RU-Azure-NonProd"

# Clone the repo (if not already done)
git clone https://github.com/sajor2000/rush_policy_rag.git
cd rush_policy_rag
```

---

## Step 1: Set Your Variables

Run these commands to set variables used throughout the deployment:

```bash
# Copy and paste this entire block
# IMPORTANT: These are the ACTUAL deployed resource names in RU-Azure-NonProd subscription
export RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG"
export LOCATION="eastus"
export ACR_NAME="aiinnovation"
export SEARCH_NAME="policychataisearch"
export OPENAI_NAME="rua-nonprod-ai-innovation"
export STORAGE_NAME="policytechrush"
export ENV_NAME="rush-policy-env-production"

# Verify variables are set
echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"
echo "ACR: $ACR_NAME"
```

**Expected output:**
```
Resource Group: RU-A-NonProd-AI-Innovation-RG
Location: eastus
ACR: aiinnovation
```

---

## Step 2: Create Resource Group

```bash
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION
```

**Expected output:**
```json
{
  "id": "/subscriptions/.../resourceGroups/RU-A-NonProd-AI-Innovation-RG",
  "location": "eastus",
  "name": "RU-A-NonProd-AI-Innovation-RG",
  "properties": { "provisioningState": "Succeeded" }
}
```

---

## Step 3: Create Azure Container Registry

```bash
az acr create \
  --name $ACR_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku Basic \
  --admin-enabled true
```

**Get ACR credentials (save these):**

```bash
# Get ACR login server
az acr show --name $ACR_NAME --query loginServer -o tsv

# Get ACR password
export ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)
echo "ACR Password: $ACR_PASSWORD"
```

**Save the password** - you'll need it in Step 8.

---

## Step 4: Create Azure AI Search

```bash
az search service create \
  --name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku basic \
  --partition-count 1 \
  --replica-count 1
```

**Get Search API key (save this):**

```bash
export SEARCH_API_KEY=$(az search admin-key show \
  --service-name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --query primaryKey -o tsv)
echo "Search API Key: $SEARCH_API_KEY"
```

---

## Step 5: Create Azure OpenAI Service

```bash
# Create the OpenAI resource
az cognitiveservices account create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --kind OpenAI \
  --sku S0 \
  --location $LOCATION

# Deploy GPT-4 model (for chat)
az cognitiveservices account deployment create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --deployment-name gpt-4.1 \
  --model-name gpt-4 \
  --model-version "1106-Preview" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard

# Deploy embedding model
az cognitiveservices account deployment create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --deployment-name text-embedding-3-large \
  --model-name text-embedding-3-large \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard
```

**Get OpenAI API key (save this):**

```bash
export AOAI_API_KEY=$(az cognitiveservices account keys list \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --query key1 -o tsv)
echo "OpenAI API Key: $AOAI_API_KEY"

export AOAI_ENDPOINT=$(az cognitiveservices account show \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --query properties.endpoint -o tsv)
echo "OpenAI Endpoint: $AOAI_ENDPOINT"
```

---

## Step 5.5: Deploy Cohere Rerank 3.5 (Azure AI Foundry)

Cohere Rerank 3.5 provides cross-encoder reranking for negation-aware search. This significantly improves retrieval quality (77.8% → 100% pass rate in testing).

**Why Cohere?**
- Cross-encoders understand negation ("NOT authorized" contradicts "Can accept verbal orders?")
- Bi-encoders (like Azure's L2 reranker) only see vocabulary overlap
- Critical for healthcare policy accuracy

### 5.5.1 Deploy via Azure AI Foundry Portal

1. Go to [Azure AI Foundry](https://ai.azure.com/)
2. Create or select a project
3. Navigate to **Model catalog** → Search "Cohere Rerank"
4. Select **Cohere Rerank 3.5** → Click **Deploy**
5. Choose **Serverless API** deployment type
6. Select your subscription and resource group
7. Accept the terms and click **Deploy**

### 5.5.2 Get Cohere API Credentials

After deployment completes:

```bash
# The endpoint URL from Azure AI Foundry deployment page
# Format: https://Cohere-rerank-v3-5-xxxxx.eastus2.models.ai.azure.com
export COHERE_RERANK_ENDPOINT="<your-cohere-endpoint-from-portal>"

# The API key from Azure AI Foundry deployment page (under "Keys")
export COHERE_RERANK_API_KEY="<your-cohere-api-key>"

echo "Cohere Endpoint: $COHERE_RERANK_ENDPOINT"
```

**Save these values** - you'll need them in Step 8.

### 5.5.3 Verify Cohere Deployment

```bash
# Test the Cohere endpoint (should return a reranked response)
curl -X POST "${COHERE_RERANK_ENDPOINT}/v1/rerank" \
  -H "Authorization: Bearer $COHERE_RERANK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cohere-rerank-v3-5",
    "query": "test query",
    "documents": ["test document"],
    "top_n": 1
  }'
```

**Expected output:** JSON with `results` array containing reranked documents.

---

## Step 6: Create Azure Blob Storage

```bash
# Create storage account
az storage account create \
  --name $STORAGE_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku Standard_LRS \
  --kind StorageV2

# Create the three required containers
az storage container create --name policies-source --account-name $STORAGE_NAME
az storage container create --name policies-active --account-name $STORAGE_NAME
az storage container create --name policies-archive --account-name $STORAGE_NAME
```

**Get storage connection string (save this):**

```bash
export STORAGE_CONNECTION_STRING=$(az storage account show-connection-string \
  --name $STORAGE_NAME \
  --resource-group $RESOURCE_GROUP \
  --query connectionString -o tsv)
echo "Storage Connection String: $STORAGE_CONNECTION_STRING"
```

---

## Step 7: Create Container Apps Environment

```bash
az containerapp env create \
  --name $ENV_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

Wait for this to complete (takes 2-3 minutes).

**Verify:**
```bash
az containerapp env show --name $ENV_NAME --resource-group $RESOURCE_GROUP --query "properties.provisioningState" -o tsv
```

**Expected output:** `Succeeded`

---

## Step 8: Build and Deploy Backend

### 8.1 Build the Backend Container Image

```bash
cd apps/backend
az acr build --registry $ACR_NAME --image policytech-backend:latest .
cd ../..
```

Wait for the build to complete (takes 3-5 minutes).

**Expected output ends with:**
```
Run ID: xxx was successful after Xm Xs
```

### 8.2 Deploy Backend Container

```bash
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
  --min-replicas 2 \
  --max-replicas 10 \
  --cpu 1.0 \
  --memory 2Gi \
  --env-vars \
    SEARCH_ENDPOINT="https://${SEARCH_NAME}.search.windows.net" \
    SEARCH_API_KEY="$SEARCH_API_KEY" \
    SEARCH_INDEX_NAME="rush-policies" \
    SEARCH_SEMANTIC_CONFIG="default-semantic" \
    AOAI_ENDPOINT="$AOAI_ENDPOINT" \
    AOAI_API_KEY="$AOAI_API_KEY" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    STORAGE_CONNECTION_STRING="$STORAGE_CONNECTION_STRING" \
    CONTAINER_NAME="policies-active" \
    USE_ON_YOUR_DATA="true" \
    USE_COHERE_RERANK="true" \
    COHERE_RERANK_ENDPOINT="$COHERE_RERANK_ENDPOINT" \
    COHERE_RERANK_API_KEY="$COHERE_RERANK_API_KEY" \
    COHERE_RERANK_MODEL="cohere-rerank-v3-5" \
    COHERE_RERANK_TOP_N="10" \
    COHERE_RERANK_MIN_SCORE="0.15" \
    BACKEND_PORT="8000" \
    LOG_FORMAT="json"
```

### 8.3 Get Backend URL

```bash
export BACKEND_URL=$(az containerapp show \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn -o tsv)
echo "Backend URL: https://$BACKEND_URL"
```

### 8.4 Verify Backend is Running

```bash
curl https://$BACKEND_URL/health
```

**Expected output:**
```json
{"status": "healthy", "timestamp": "...", "version": "3.0.0"}
```

---

## Step 9: Build and Deploy Frontend

### 9.1 Build the Frontend Container Image

```bash
cd apps/frontend
az acr build --registry $ACR_NAME --image policytech-frontend:latest --build-arg BACKEND_URL=https://$BACKEND_URL .
cd ../..
```

### 9.2 Deploy Frontend Container

```bash
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
  --min-replicas 2 \
  --max-replicas 10 \
  --cpu 0.5 \
  --memory 1Gi \
  --env-vars \
    BACKEND_URL="https://$BACKEND_URL" \
    NEXT_PUBLIC_API_URL="https://$BACKEND_URL" \
    NODE_ENV="production"
```

### 9.3 Get Frontend URL

```bash
export FRONTEND_URL=$(az containerapp show \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn -o tsv)
echo "Frontend URL: https://$FRONTEND_URL"
```

---

## Step 10: Update Backend CORS

Now that you have the frontend URL, update the backend CORS policy:

```bash
az containerapp update \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --set-env-vars CORS_ORIGINS="https://$FRONTEND_URL"
```

---

## Step 11: Verify Deployment

### Test Backend Health

```bash
curl https://$BACKEND_URL/health
```

### Test Chat Endpoint

```bash
curl -X POST "https://$BACKEND_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the chaperone policy?"}'
```

### Test Frontend

Open in browser: `https://$FRONTEND_URL`

You should see the RUSH Policy chat interface.

---

## Step 12: Optional - Add Custom Domain

If you want to use `policy.rush.edu` instead of the Azure URL:

```bash
# Add custom domain to frontend
az containerapp hostname add \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --hostname policy.rush.edu

# Get validation token for DNS
az containerapp hostname list \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP

# After adding DNS records, bind SSL certificate
az containerapp hostname bind \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --hostname policy.rush.edu \
  --environment $ENV_NAME
```

---

## Post-Deployment Verification Checklist

### Backend

- [ ] `curl https://<backend>/health` returns `{"status": "healthy"}`
- [ ] `https://<backend>/docs` shows Swagger API documentation
- [ ] `POST /api/chat` with test query returns policy answer

### Frontend

- [ ] Frontend loads in browser
- [ ] Chat interface displays correctly
- [ ] Sending a message returns a response
- [ ] Policy citations display with clickable links
- [ ] "View PDF" buttons work

### Integration

- [ ] No CORS errors in browser console (F12 → Console)
- [ ] Response times under 5 seconds
- [ ] Mobile view works correctly

---

## Troubleshooting

### Container Won't Start

```bash
# Check container logs
az containerapp logs show \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --follow
```

### CORS Errors

Make sure the frontend URL is in the backend's CORS policy:

```bash
# Check current environment variables
az containerapp show \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --query "properties.template.containers[0].env"
```

### Search Returns Empty

1. Verify the search index exists and has documents
2. Check SEARCH_API_KEY is correct
3. Test directly: `https://<search>.search.windows.net/indexes/rush-policies/docs?api-version=2023-11-01&search=*`

### Chat Returns 500 Error

1. Check Azure OpenAI credentials
2. Verify model deployments exist: `gpt-4.1` and `text-embedding-3-large`
3. Check backend logs for specific error

---

## Quick Reference - All URLs

| Service | URL |
|---------|-----|
| **Frontend** | `https://rush-policy-frontend.<region>.azurecontainerapps.io` |
| **Backend API** | `https://rush-policy-backend.<region>.azurecontainerapps.io` |
| **Backend Health** | `https://rush-policy-backend.<region>.azurecontainerapps.io/health` |
| **API Docs** | `https://rush-policy-backend.<region>.azurecontainerapps.io/docs` |

---

## Updating After Code Changes

When you need to update the deployed application:

### Update Backend Only

```bash
cd apps/backend
az acr build --registry $ACR_NAME --image policytech-backend:latest .
az containerapp update \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --image ${ACR_NAME}.azurecr.io/policytech-backend:latest
```

### Update Frontend Only

```bash
cd apps/frontend
az acr build --registry $ACR_NAME --image policytech-frontend:latest .
az containerapp update \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --image ${ACR_NAME}.azurecr.io/policytech-frontend:latest
```

---

## Environment Variables Reference

### Backend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `SEARCH_ENDPOINT` | Azure AI Search URL | `https://policychataisearch.search.windows.net` |
| `SEARCH_API_KEY` | Search admin API key | `abc123...` |
| `SEARCH_INDEX_NAME` | Search index name | `rush-policies` |
| `SEARCH_SEMANTIC_CONFIG` | Semantic config name | `default-semantic` |
| `AOAI_ENDPOINT` | Azure OpenAI URL | `https://policytech-openai.openai.azure.com/` |
| `AOAI_API_KEY` | OpenAI API key | `abc123...` |
| `AOAI_CHAT_DEPLOYMENT` | Chat model name | `gpt-4.1` |
| `AOAI_EMBEDDING_DEPLOYMENT` | Embedding model name | `text-embedding-3-large` |
| `STORAGE_CONNECTION_STRING` | Blob storage connection | `DefaultEndpointsProtocol=https;...` |
| `CONTAINER_NAME` | PDF container | `policies-active` |
| `USE_ON_YOUR_DATA` | Enable hybrid search | `true` |
| `BACKEND_PORT` | Server port | `8000` |
| `LOG_FORMAT` | Logging format | `json` |

### Backend - Cohere Rerank (Required for best quality)

| Variable | Description | Example |
|----------|-------------|---------|
| `USE_COHERE_RERANK` | Enable Cohere cross-encoder | `true` |
| `COHERE_RERANK_ENDPOINT` | Azure AI Foundry endpoint | `https://Cohere-rerank-v3-5-xxx.eastus2.models.ai.azure.com` |
| `COHERE_RERANK_API_KEY` | Cohere API key | `abc123...` |
| `COHERE_RERANK_MODEL` | Model name | `cohere-rerank-v3-5` |
| `COHERE_RERANK_TOP_N` | Docs after rerank | `10` |
| `COHERE_RERANK_MIN_SCORE` | Relevance threshold | `0.15` |

> **Note:** Cohere Rerank improves pass rate from 77.8% to 100% by understanding negation in queries like "Can MA accept verbal orders?" (answer: NOT authorized).

### Frontend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `BACKEND_URL` | Backend API URL | `https://rush-policy-backend.azurecontainerapps.io` |
| `NEXT_PUBLIC_API_URL` | Client-side API URL | `https://rush-policy-backend.azurecontainerapps.io` |
| `NODE_ENV` | Environment | `production` |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     AZURE CONTAINER APPS ENVIRONMENT                          │
│                     (rush-policy-env-production)                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   ┌─────────────────────────┐         ┌─────────────────────────────────┐    │
│   │  rush-policy-frontend   │         │  rush-policy-backend            │    │
│   │  ─────────────────────  │  HTTP   │  ─────────────────────          │    │
│   │  Next.js 14             │────────▶│  FastAPI (Python 3.12)          │    │
│   │  Port: 3000             │         │  Port: 8000                     │    │
│   │  Replicas: 2-10         │         │  Replicas: 2-10                 │    │
│   │  CPU: 0.5 | RAM: 1Gi    │         │  CPU: 1.0 | RAM: 2Gi            │    │
│   └─────────────────────────┘         └──────────────┬──────────────────┘    │
│                                                       │                       │
└───────────────────────────────────────────────────────┼───────────────────────┘
                                                        │
          ┌─────────────────────────────────────────────┼─────────────────────────────┐
          │                    │                        │                    │        │
          ▼                    ▼                        ▼                    ▼        │
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐ ┌───────────────┐  │
│  Azure AI Search  │ │  Azure OpenAI     │ │  Cohere Rerank    │ │ Azure Blob    │  │
│  ───────────────  │ │  ───────────────  │ │  ───────────────  │ │ Storage       │  │
│  Index:           │ │  Models:          │ │  Model:           │ │ ───────────── │  │
│  rush-policies    │ │  • gpt-4.1        │ │  rerank-v3-5      │ │ policies-     │  │
│  Vectors: 3072-dim│ │  • text-embedding │ │  (cross-encoder)  │ │ active/       │  │
│  Semantic ranker  │ │    -3-large       │ │  Azure AI Foundry │ │               │  │
└───────────────────┘ └───────────────────┘ └───────────────────┘ └───────────────┘  │
                                                                                      │
          ◄───────────────────── RAG PIPELINE FLOW ──────────────────────────────────►
          1. Query → AI Search   2. Rerank with Cohere   3. Generate with GPT-4.1
```

---

## Alternative: Bicep Template Deployment

If you prefer Infrastructure-as-Code or are using GitHub Actions CI/CD, use the Bicep templates instead of manual CLI deployment.

### Prerequisites

1. Container images must be built and pushed to GitHub Container Registry (GHCR)
2. All Azure services (AI Search, OpenAI, Blob Storage, Cohere) must be created first (Steps 4-6 above)
3. Container Apps Environment must exist (Step 7 above)

### Step 1: Create Parameters File

Create `infrastructure/parameters.json` from the template:

```bash
cp infrastructure/parameters.json.template infrastructure/parameters.json
# Edit with your actual values
```

Required parameters:
- `containerImage`: GHCR image URL (e.g., `ghcr.io/YOUR_ORG/rush_policy_rag/backend:latest`)
- `registryPassword`: GitHub PAT with `packages:read` scope
- `searchEndpoint`, `searchApiKey`: From Azure AI Search
- `aoaiEndpoint`, `aoaiApiKey`: From Azure OpenAI
- `storageConnectionString`: From Azure Blob Storage
- `cohereRerankerEndpoint`, `cohereRerankerApiKey`: From Azure AI Foundry
- `appInsightsConnectionString`: From Application Insights (optional)

### Step 2: Deploy Backend

```bash
az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file infrastructure/azure-container-app.bicep \
  --parameters @infrastructure/parameters.json \
  --parameters environment=production
```

### Step 3: Deploy Frontend

```bash
az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file infrastructure/azure-container-app-frontend.bicep \
  --parameters environment=production \
  --parameters backendUrl="https://rush-policy-backend-production.$(az group show -n $RESOURCE_GROUP --query location -o tsv).azurecontainerapps.io"
```

### Container Registry Options

**Option A: GitHub Container Registry (GHCR)** - Used by CI/CD workflow
- Images: `ghcr.io/YOUR_ORG/rush_policy_rag/backend:latest`
- Auth: GitHub PAT with `packages:read` scope
- Bicep templates default to GHCR

**Option B: Azure Container Registry (ACR)** - Used by manual CLI deployment
- Images: `${ACR_NAME}.azurecr.io/policytech-backend:latest`
- Auth: ACR admin credentials or managed identity
- Requires updating Bicep `registries` section

---

## Support

- **Documentation**: See `CLAUDE.md` for development guidance
- **Issues**: https://github.com/sajor2000/rush_policy_rag/issues
- **Policy Admin**: https://rushumc.navexone.com/