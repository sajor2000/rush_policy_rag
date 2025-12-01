# Deployment Guide - RUSH Policy RAG Agent

> **IMPORTANT FOR DEPLOYMENT TEAM**
>
> ## Architecture: FastAPI + Next.js ONLY
>
> ```
> ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
> │   Browser   │────▶│  Next.js    │────▶│   FastAPI   │
> │             │     │  Frontend   │     │   Backend   │
> └─────────────┘     │  (3000)     │     │   (8000)    │
>                     └─────────────┘     └─────────────┘
> ```
>
> **NO AZURE FUNCTIONS** | **NO REDIS** | **NO SERVERLESS**
>
> Deploy only:
> - `apps/backend/` → Azure Container Apps (FastAPI)
> - `apps/frontend/` → Azure Container Apps (Next.js)

---

## Quick Reference for Deployment Team

| What | URL After Deployment |
|------|---------------------|
| **Frontend** | `https://rush-policy-frontend.azurecontainerapps.io` |
| **Backend API** | `https://rush-policy-backend.azurecontainerapps.io` |
| **Health Check** | `https://rush-policy-backend.azurecontainerapps.io/health` |
| **API Docs** | `https://rush-policy-backend.azurecontainerapps.io/docs` |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                        RUSH POLICY RAG - PRODUCTION ARCHITECTURE                      │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                       │
│                              ┌─────────────────────┐                                  │
│                              │   User's Browser    │                                  │
│                              │  (policy.rush.edu)  │                                  │
│                              └──────────┬──────────┘                                  │
│                                         │                                             │
│                                         ▼                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │                         AZURE CONTAINER APPS                                   │   │
│  │                                                                                │   │
│  │  ┌────────────────────────────┐      ┌────────────────────────────────────┐   │   │
│  │  │      FRONTEND              │      │           BACKEND                  │   │   │
│  │  │  rush-policy-frontend      │      │    rush-policy-backend             │   │   │
│  │  │  ────────────────────      │      │    ──────────────────              │   │   │
│  │  │  • Next.js 14              │ HTTP │    • FastAPI (Python 3.12)         │   │   │
│  │  │  • RUSH Brand UI           │─────▶│    • Rate Limiting (30/min)        │   │   │
│  │  │  • Port 3000               │      │    • Port 8000                     │   │   │
│  │  │                            │      │                                    │   │   │
│  │  │  Image Size: ~150MB        │      │    Image Size: ~800MB              │   │   │
│  │  └────────────────────────────┘      └──────────────┬─────────────────────┘   │   │
│  │                                                      │                         │   │
│  └──────────────────────────────────────────────────────┼─────────────────────────┘   │
│                                                         │                             │
│                          ┌──────────────────────────────┴──────────────────────────┐  │
│                          │                                                          │  │
│                          ▼                              ▼                           ▼  │
│  ┌─────────────────────────────┐  ┌─────────────────────────────┐  ┌─────────────────┐ │
│  │   AZURE AI SEARCH           │  │   AZURE OPENAI              │  │ AZURE BLOB      │ │
│  │   ─────────────────         │  │   ────────────              │  │ STORAGE         │ │
│  │   Index: rush-policies      │  │   GPT-4.1 (Chat)            │  │ ────────────    │ │
│  │   • 3072-dim vectors        │  │   text-embedding-3-large    │  │ policies-active │ │
│  │   • Semantic ranker         │  │   (Embeddings)              │  │ (PDF files)     │ │
│  │   • 132 synonym rules       │  │                             │  │                 │ │
│  └─────────────────────────────┘  └─────────────────────────────┘  └─────────────────┘ │
│                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────┘

DATA FLOW:
──────────
1. User asks: "What is the chaperone policy?"
2. Frontend → Backend (POST /api/chat)
3. Backend → Azure OpenAI "On Your Data" (vectorSemanticHybrid search)
4. Azure OpenAI → Azure AI Search (vector + keyword + reranking)
5. Response with citations → User
```

---

## What Gets Deployed

| Component | Technology | Azure Service | Port |
|-----------|------------|---------------|------|
| **Frontend** | Next.js 14 (React) | Azure Container Apps | 3000 |
| **Backend** | FastAPI (Python 3.12) | Azure Container Apps | 8000 |
| **Vector Store** | Azure AI Search | Azure AI Search | N/A |
| **LLM** | GPT-4.1 | Azure OpenAI | N/A |
| **Embeddings** | text-embedding-3-large | Azure OpenAI | N/A |
| **PDF Storage** | Blob Storage | Azure Storage | N/A |

---

## Step-by-Step Deployment

### Prerequisites Checklist

- [ ] Azure CLI installed (`az --version`)
- [ ] Logged into Azure (`az login`)
- [ ] Access to Rush Azure subscription
- [ ] Git repo cloned locally

---

### STEP 1: Create Azure Resources (One-Time Setup)

Run these commands to create all required Azure services:

```bash
# Set your variables
RESOURCE_GROUP="rg-policytech-prod"
LOCATION="eastus"
ACR_NAME="policytechacr"           # Container Registry
SEARCH_NAME="policychataisearch"   # AI Search
OPENAI_NAME="policytech-openai"    # OpenAI
STORAGE_NAME="policytechrush"      # Blob Storage

# 1. Create Resource Group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 2. Create Container Registry (stores Docker images)
az acr create \
  --name $ACR_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku Basic \
  --admin-enabled true

# 3. Create Azure AI Search
az search service create \
  --name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku basic

# 4. Create Azure OpenAI
az cognitiveservices account create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --kind OpenAI \
  --sku S0 \
  --location $LOCATION

# 5. Deploy OpenAI Models
az cognitiveservices account deployment create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --deployment-name gpt-4.1 \
  --model-name gpt-4 \
  --model-version "1106-Preview" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard

az cognitiveservices account deployment create \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --deployment-name text-embedding-3-large \
  --model-name text-embedding-3-large \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard

# 6. Create Storage Account + Containers
az storage account create \
  --name $STORAGE_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku Standard_LRS

az storage container create --name policies-source --account-name $STORAGE_NAME
az storage container create --name policies-active --account-name $STORAGE_NAME
az storage container create --name policies-archive --account-name $STORAGE_NAME

# 7. Create Container Apps Environment
az containerapp env create \
  --name policytech-env \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

---

### STEP 2: Get API Keys

After creating resources, retrieve your API keys:

```bash
# Get Search API Key
az search admin-key show \
  --service-name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --query primaryKey -o tsv

# Get OpenAI API Key
az cognitiveservices account keys list \
  --name $OPENAI_NAME \
  --resource-group $RESOURCE_GROUP \
  --query key1 -o tsv

# Get Storage Connection String
az storage account show-connection-string \
  --name $STORAGE_NAME \
  --resource-group $RESOURCE_GROUP \
  --query connectionString -o tsv

# Get ACR Password
az acr credential show \
  --name $ACR_NAME \
  --query passwords[0].value -o tsv
```

**Save these values** - you'll need them in Step 4.

---

### STEP 3: Build Container Images

```bash
# Navigate to project root
cd /path/to/rag_pt_rush

# Build Backend image
cd apps/backend
az acr build --registry $ACR_NAME --image policytech-backend:latest .

# Build Frontend image
cd ../frontend
az acr build --registry $ACR_NAME --image policytech-frontend:latest .
```

---

### STEP 4: Deploy Backend

```bash
# Create secrets (replace <VALUES> with real keys from Step 2)
az containerapp secret set \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --secrets \
    search-api-key="<SEARCH_API_KEY>" \
    aoai-api-key="<OPENAI_API_KEY>" \
    storage-conn="<STORAGE_CONNECTION_STRING>" \
    admin-key="<YOUR_ADMIN_KEY>"

# Deploy Backend
az containerapp create \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --environment policytech-env \
  --image $ACR_NAME.azurecr.io/policytech-backend:latest \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password "<ACR_PASSWORD>" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --env-vars \
    SEARCH_ENDPOINT="https://$SEARCH_NAME.search.windows.net" \
    SEARCH_API_KEY="secretref:search-api-key" \
    SEARCH_INDEX_NAME="rush-policies" \
    AOAI_ENDPOINT="https://$OPENAI_NAME.openai.azure.com/" \
    AOAI_API="secretref:aoai-api-key" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    STORAGE_CONNECTION_STRING="secretref:storage-conn" \
    CONTAINER_NAME="policies-active" \
    ADMIN_API_KEY="secretref:admin-key" \
    USE_ON_YOUR_DATA="true" \
    BACKEND_PORT="8000"
```

---

### STEP 5: Deploy Frontend

```bash
# Get Backend URL first
BACKEND_URL=$(az containerapp show \
  --name rush-policy-backend \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn -o tsv)

# Deploy Frontend
az containerapp create \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --environment policytech-env \
  --image $ACR_NAME.azurecr.io/policytech-frontend:latest \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password "<ACR_PASSWORD>" \
  --target-port 3000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5 \
  --env-vars \
    NEXT_PUBLIC_API_URL="https://$BACKEND_URL" \
    NODE_ENV="production"
```

---

### STEP 6: Verify Deployment

```bash
# Get your URLs
echo "Frontend: https://$(az containerapp show --name rush-policy-frontend --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv)"
echo "Backend:  https://$(az containerapp show --name rush-policy-backend --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv)"

# Test health endpoint
curl https://rush-policy-backend.azurecontainerapps.io/health
# Expected: {"status": "healthy", ...}

# Test chat endpoint
curl -X POST https://rush-policy-backend.azurecontainerapps.io/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the chaperone policy?"}'
```

---

### STEP 7: Add Custom Domain (Optional)

```bash
# Add custom domain (e.g., policy.rush.edu)
az containerapp hostname add \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --hostname policy.rush.edu

# Bind SSL certificate
az containerapp hostname bind \
  --name rush-policy-frontend \
  --resource-group $RESOURCE_GROUP \
  --hostname policy.rush.edu \
  --certificate <CERTIFICATE_NAME>
```

---

## Deployment Verification Checklist

After deployment, verify:

### Backend ✅
- [ ] Health check returns 200: `curl https://<backend>/health`
- [ ] API docs accessible: `https://<backend>/docs`
- [ ] Chat endpoint works: `POST /api/chat`
- [ ] PDF endpoint works: `GET /api/pdf/<filename>.pdf`

### Frontend ✅
- [ ] App loads at frontend URL
- [ ] Chat interface displays correctly
- [ ] User can send messages and get responses
- [ ] Policy citations display with links
- [ ] PDF viewer opens documents

### Integration ✅
- [ ] No CORS errors in browser console
- [ ] Response times under 5 seconds
- [ ] Mobile responsive layout works

---

## Architecture Deep Dive

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  PRODUCTION ARCHITECTURE (FastAPI + Next.js)                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   User Browser                                                               │
│        │                                                                     │
│        ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     Azure Container Apps                             │   │
│   │  ┌───────────────────────┐       ┌─────────────────────────────┐   │   │
│   │  │ rush-policy-frontend  │ HTTP  │ rush-policy-backend         │   │   │
│   │  │ (Next.js 14)          │──────►│ (FastAPI)                   │   │   │
│   │  │ ├── Security Headers  │       │ ├── Rate Limiting (30/min)  │   │   │
│   │  │ └── CSP, HSTS         │       │ ├── Input Validation        │   │   │
│   │  └───────────────────────┘       │ └── Azure AD Auth (optional)│   │   │
│   │                                  └─────────────┬───────────────┘   │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                    │                         │
│                                                    ▼                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │           Azure OpenAI "On Your Data" (vectorSemanticHybrid)         │   │
│   │  ├── GPT-4.1 Chat Completions API                                    │   │
│   │  └── Integrated Search with L2 Semantic Reranking                   │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                       │
│                   ┌──────────────────┼──────────────────┐                   │
│                   │                  │                  │                   │
│                   ▼                  ▼                  ▼                   │
│            ┌──────────┐       ┌──────────┐       ┌──────────┐              │
│            │  Azure   │       │  Azure   │       │  Azure   │              │
│            │   AI     │       │  OpenAI  │       │  Blob    │              │
│            │  Search  │       │ (GPT-4.1)│       │ Storage  │              │
│            │rush-policies     │          │       │policies-active          │
│            └──────────┘       └──────────┘       └──────────┘              │
│                                                                              │
│   NO AZURE FUNCTIONS - Pure FastAPI + Next.js                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Stack Components:**
- **Frontend**: Next.js 14 (App Router) → Azure Container Apps
- **Backend**: FastAPI → Azure Container Apps
- **Search**: Azure OpenAI "On Your Data" with vectorSemanticHybrid (Vector + BM25 + L2 Reranking)
- **LLM**: Azure OpenAI (GPT-4.1)
- **Storage**: Azure Blob Storage (Policy PDFs)

**Architecture Status**: ✅ COMPLETED - See [`docs/SINGLE_BACKEND_SIMPLIFICATION.md`](docs/SINGLE_BACKEND_SIMPLIFICATION.md)

---

## Step-by-Step Deployment Checklist

### Azure Resources Required

Before deploying, ensure these Azure resources are created and configured:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        REQUIRED AZURE RESOURCES                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  1. AZURE AI SEARCH (Vector Store)                                   │    │
│  │     ├── Service Tier: Basic or Standard (for semantic ranker)       │    │
│  │     ├── Index Name: rush-policies                                   │    │
│  │     ├── Vector Config: 3072 dimensions (text-embedding-3-large)     │    │
│  │     ├── Semantic Config: my-semantic-config (for L2 reranking)      │    │
│  │     └── Synonym Map: 132 healthcare synonym rules                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  2. AZURE OPENAI SERVICE                                             │    │
│  │     ├── Deployment 1: gpt-4.1 (Chat Completions)                    │    │
│  │     ├── Deployment 2: text-embedding-3-large (Embeddings)           │    │
│  │     └── Region: Must support "On Your Data" feature                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  3. AZURE BLOB STORAGE (PDF Storage)                                 │    │
│  │     ├── Container: policies-source (staging/upload area)           │    │
│  │     ├── Container: policies-active (production PDFs)                │    │
│  │     ├── Container: policies-archive (deleted policies)              │    │
│  │     └── Access: Private with SAS token generation                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  4. AZURE CONTAINER REGISTRY                                         │    │
│  │     └── SKU: Basic (sufficient for most deployments)                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  5. AZURE CONTAINER APPS ENVIRONMENT                                 │    │
│  │     ├── Backend Container: rush-policy-backend (FastAPI)            │    │
│  │     └── Frontend Container: rush-policy-frontend (Next.js)          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  6. AZURE AD APP REGISTRATION (Optional - for authentication)       │    │
│  │     ├── Client ID for backend                                       │    │
│  │     └── Tenant ID for Rush Azure tenant                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Resource | Purpose | Required? | SKU/Tier |
|----------|---------|-----------|----------|
| **Azure AI Search** | Vector store + semantic ranking | ✅ Yes | Basic or Standard |
| **Azure OpenAI** | GPT-4.1 + embeddings | ✅ Yes | S0 |
| **Azure Blob Storage** | PDF document storage | ✅ Yes | Standard LRS |
| **Azure Container Registry** | Container images | ✅ Yes | Basic |
| **Azure Container Apps** | Host frontend + backend | ✅ Yes | Consumption |
| **Azure AD App Registration** | Authentication | Optional | N/A |

### Create Azure Resources (One-Time Setup)

```bash
# 1. Create Resource Group
az group create --name rg-policytech-prod --location eastus

# 2. Create Azure AI Search (with semantic ranker support)
az search service create \
  --name policychataisearch \
  --resource-group rg-policytech-prod \
  --sku basic \
  --partition-count 1 \
  --replica-count 1

# 3. Create Azure OpenAI Service
az cognitiveservices account create \
  --name policytech-openai \
  --resource-group rg-policytech-prod \
  --kind OpenAI \
  --sku S0 \
  --location eastus

# 4. Deploy OpenAI Models
az cognitiveservices account deployment create \
  --name policytech-openai \
  --resource-group rg-policytech-prod \
  --deployment-name gpt-4.1 \
  --model-name gpt-4 \
  --model-version "1106-Preview" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard

az cognitiveservices account deployment create \
  --name policytech-openai \
  --resource-group rg-policytech-prod \
  --deployment-name text-embedding-3-large \
  --model-name text-embedding-3-large \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name Standard

# 5. Create Storage Account with Blob Containers
az storage account create \
  --name policytechrush \
  --resource-group rg-policytech-prod \
  --sku Standard_LRS \
  --kind StorageV2

az storage container create --name policies-source --account-name policytechrush
az storage container create --name policies-active --account-name policytechrush
az storage container create --name policies-archive --account-name policytechrush

# 6. Create Container Registry
az acr create \
  --name policytechacr \
  --resource-group rg-policytech-prod \
  --sku Basic \
  --admin-enabled true

# 7. Create Container Apps Environment
az containerapp env create \
  --name policytech-env \
  --resource-group rg-policytech-prod \
  --location eastus
```

### Initialize Search Index (One-Time)

After creating resources, initialize the Azure AI Search index:

```bash
cd apps/backend
source ../../.venv/bin/activate

# Create index schema with vector config and semantic ranker
python scripts/setup_azure_infrastructure.py

# Upload synonym map (132 healthcare rules)
python azure_policy_index.py synonyms

# Verify index was created
python azure_policy_index.py stats
```

### Deployment Steps

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DEPLOYMENT WORKFLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Step 1: Prerequisites                                                       │
│  ├── az login (Azure CLI)                                                   │
│  ├── Create Resource Group                                                  │
│  └── Create Container Registry                                              │
│                                                                              │
│  Step 2: Deploy Backend                                                      │
│  ├── az acr build (build container image)                                   │
│  ├── az containerapp env create (create environment)                        │
│  ├── az containerapp secret set (add secrets)                               │
│  └── az containerapp create (deploy backend)                                │
│                                                                              │
│  Step 3: Deploy Frontend                                                     │
│  ├── az acr build (build container image)                                   │
│  └── az containerapp create (deploy frontend with BACKEND_URL)              │
│                                                                              │
│  Step 4: Verify                                                              │
│  ├── curl https://<backend>/health                                          │
│  ├── Test chat endpoint                                                     │
│  └── Test PDF viewing                                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Production Environment Variables

Create secrets for sensitive values:
```bash
az containerapp secret set \
  --name policytech-backend \
  --resource-group <RG_NAME> \
  --secrets \
    search-api-key=<SEARCH_API_KEY> \
    aoai-api-key=<AOAI_API_KEY> \
    storage-conn=<STORAGE_CONNECTION_STRING> \
    admin-key=<ADMIN_API_KEY>
```

Required environment variables:
```bash
# Core Services
SEARCH_ENDPOINT=https://<search>.search.windows.net
SEARCH_API_KEY=secretref:search-api-key
AOAI_ENDPOINT=https://<openai>.openai.azure.com/
AOAI_API_KEY=secretref:aoai-api-key
AOAI_CHAT_DEPLOYMENT=gpt-4.1
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
STORAGE_CONNECTION_STRING=secretref:storage-conn
CONTAINER_NAME=policies-active

# Production Security
USE_ON_YOUR_DATA=true
FAIL_ON_MISSING_CONFIG=true
CORS_ORIGINS=https://rush-policy-frontend.<region>.azurecontainerapps.io
ADMIN_API_KEY=secretref:admin-key
```

---

## Option 1: Container Deployment (Recommended for Production)

Best for: Production environments, predictable scaling, isolation

### Backend - Azure Container Registry + Container Apps

#### Step 1: Build and Push Container Image

```bash
# Login to Azure
az login

# Create Container Registry (if needed)
az acr create \
  --resource-group <RG_NAME> \
  --name <ACR_NAME> \
  --sku Basic

# Build and push image
cd apps/backend
az acr build --registry <ACR_NAME> --image policytech-backend:latest .
```

#### Step 2: Deploy to Azure Container Apps

```bash
# Create Container Apps Environment (if needed)
az containerapp env create \
  --name policytech-env \
  --resource-group <RG_NAME> \
  --location eastus

# Create secrets for sensitive values
az containerapp secret set \
  --name policytech-backend \
  --resource-group <RG_NAME> \
  --secrets \
    search-api-key=<SEARCH_API_KEY> \
    aoai-api-key=<AOAI_API_KEY> \
    storage-conn=<STORAGE_CONNECTION_STRING> \
    admin-key=<ADMIN_API_KEY>

# Deploy container
az containerapp create \
  --name policytech-backend \
  --resource-group <RG_NAME> \
  --environment policytech-env \
  --image <ACR_NAME>.azurecr.io/policytech-backend:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --env-vars \
    SEARCH_ENDPOINT=https://<search-service>.search.windows.net \
    SEARCH_API_KEY=secretref:search-api-key \
    SEARCH_INDEX_NAME=rush-policies \
    AOAI_ENDPOINT=https://<aoai-service>.openai.azure.com/ \
    AOAI_API_KEY=secretref:aoai-api-key \
    AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large \
    AOAI_CHAT_DEPLOYMENT=gpt-4.1 \
    STORAGE_CONNECTION_STRING=secretref:storage-conn \
    CONTAINER_NAME=policies-active \
    ADMIN_API_KEY=secretref:admin-key \
    USE_ON_YOUR_DATA=true \
    FAIL_ON_MISSING_CONFIG=true \
    CORS_ORIGINS=https://rush-policy-frontend.<REGION>.azurecontainerapps.io \
    BACKEND_PORT=8000
```

#### Alternative: Azure App Service (Container)

```bash
# Create App Service Plan
az appservice plan create \
  --name policytech-plan \
  --resource-group <RG_NAME> \
  --sku B1 \
  --is-linux

# Create Web App with container
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-backend \
  --deployment-container-image-name <ACR_NAME>.azurecr.io/policytech-backend:latest

# Configure ACR credentials
az webapp config container set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --container-registry-url https://<ACR_NAME>.azurecr.io \
  --container-registry-user <ACR_USERNAME> \
  --container-registry-password <ACR_PASSWORD>
```

### Frontend - Azure Container Apps

#### Step 1: Build and Push Frontend Container Image

```bash
# Build and push frontend image
cd apps/frontend
az acr build --registry <ACR_NAME> --image policytech-frontend:latest .
```

#### Step 2: Deploy Frontend to Azure Container Apps

Using Bicep (recommended):

```bash
# Deploy using Bicep template
az deployment group create \
  --resource-group <RG_NAME> \
  --template-file infrastructure/azure-container-app-frontend.bicep \
  --parameters \
    environment=production \
    containerImage=<ACR_NAME>.azurecr.io/policytech-frontend:latest \
    registryPassword=<ACR_PASSWORD> \
    backendUrl=https://rush-policy-backend-production.<REGION>.azurecontainerapps.io
```

Or using Azure CLI:

```bash
# Deploy frontend container
az containerapp create \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --environment rush-policy-env-production \
  --image <ACR_NAME>.azurecr.io/policytech-frontend:latest \
  --target-port 3000 \
  --ingress external \
  --min-replicas 2 \
  --max-replicas 10 \
  --env-vars \
    BACKEND_URL=https://rush-policy-backend-production.<REGION>.azurecontainerapps.io \
    NODE_ENV=production
```

#### Step 3: Configure Custom Domain (Optional)

```bash
# Add custom domain (e.g., policy.rush.edu)
az containerapp hostname add \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --hostname policy.rush.edu

# Bind certificate
az containerapp hostname bind \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --hostname policy.rush.edu \
  --certificate <CERTIFICATE_NAME>
```

---

## Option 2: Source Deployment (Simpler Setup)

Best for: Development, staging, quick iterations

### Backend - Azure App Service (Python Runtime)

#### Step 1: Create App Service

```bash
# Create App Service Plan (if needed)
az appservice plan create \
  --name policytech-plan \
  --resource-group <RG_NAME> \
  --sku B1 \
  --is-linux

# Create Web App with Python runtime
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-backend \
  --runtime "PYTHON:3.11"
```

#### Step 2: Configure Startup

```bash
# Set startup command
az webapp config set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --startup-file "cd apps/backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 8000"
```

#### Step 3: Deploy from GitHub

```bash
# Configure deployment source
az webapp deployment source config \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --repo-url https://github.com/<org>/<repo> \
  --branch main \
  --manual-integration
```

Or use GitHub Actions:

```yaml
# .github/workflows/deploy-backend.yml
name: Deploy Backend to Azure

on:
  push:
    branches: [main]
    paths:
      - 'apps/backend/**'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: azure/webapps-deploy@v2
        with:
          app-name: policytech-backend
          publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
          package: apps/backend
```

#### Step 4: Set Environment Variables

```bash
az webapp config appsettings set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --settings \
    SEARCH_ENDPOINT="https://<search-service>.search.windows.net" \
    SEARCH_API_KEY="<value>" \
    SEARCH_INDEX_NAME="rush-policies" \
    AOAI_ENDPOINT="https://<aoai-service>.openai.azure.com/" \
    AOAI_API_KEY="<value>" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    STORAGE_CONNECTION_STRING="<value>" \
    CONTAINER_NAME="policies-active" \
    ADMIN_API_KEY="<value>" \
    USE_ON_YOUR_DATA="true" \
    FAIL_ON_MISSING_CONFIG="true" \
    CORS_ORIGINS="https://policytech-frontend.azurewebsites.net" \
    BACKEND_PORT="8000"
```

### Frontend - Azure App Service (Node.js Runtime)

```bash
# Create App Service for frontend
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-frontend \
  --runtime "NODE:18-lts"

# Configure startup
az webapp config set \
  --resource-group <RG_NAME> \
  --name policytech-frontend \
  --startup-file "cd apps/frontend && npm install && npm run build && npm start"

# Set environment variables
az webapp config appsettings set \
  --resource-group <RG_NAME> \
  --name policytech-frontend \
  --settings \
    BACKEND_URL="https://policytech-backend.azurecontainerapps.io" \
    NODE_ENV="production"
```

---

## Environment Variables Reference

### Backend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `SEARCH_ENDPOINT` | Azure AI Search endpoint URL | `https://mysearch.search.windows.net` |
| `SEARCH_API_KEY` | Search admin API key | `abc123...` |
| `AOAI_ENDPOINT` | Azure OpenAI endpoint | `https://<aoai>.openai.azure.com/` |
| `AOAI_API_KEY` | Azure OpenAI API key | `abc123...` |
| `AOAI_CHAT_DEPLOYMENT` | Chat model deployment name | `gpt-4.1` |
| `AOAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment | `text-embedding-3-large` |
| `STORAGE_CONNECTION_STRING` | Azure Storage connection string | `DefaultEndpointsProtocol=https;...` |
| `CONTAINER_NAME` | Blob container name | `policies-active` |

### Backend (Security - Production)

| Variable | Description | Example |
|----------|-------------|---------|
| `REQUIRE_AAD_AUTH` | Enable Azure AD authentication | `true` |
| `FAIL_ON_MISSING_CONFIG` | Fail on missing critical config | `true` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | `https://rush-policy-frontend.azurecontainerapps.io` |
| `ADMIN_API_KEY` | Admin endpoints protection key | `secure-random-key` |
| `LOG_FORMAT` | Logging format for App Insights | `json` |

### Backend (Feature Flags)

| Variable | Description | Default |
|----------|-------------|---------|
| `USE_ON_YOUR_DATA` | Enable vectorSemanticHybrid search | `true` |
| `BACKEND_PORT` | Server port | `8000` |

### Frontend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `BACKEND_URL` | Full URL to backend API | `https://policytech-backend.azurecontainerapps.io` |
| `NODE_ENV` | Node environment | `production` |

---

## CORS Configuration

The backend CORS policy is configured in the Bicep template (`infrastructure/azure-container-app.bicep`):

```bicep
corsPolicy: {
  allowedOrigins: [
    'https://rush-policy-frontend.${containerAppEnvironment.properties.defaultDomain}'
    'https://${customFrontendDomain}'  // Optional custom domain
  ]
  allowedMethods: ['GET', 'POST', 'OPTIONS']
  allowedHeaders: ['Content-Type', 'Authorization', 'X-Admin-Key']
  maxAge: 86400
}
```

For local development, CORS is configured in `apps/backend/app/core/config.py`:

```python
ALLOWED_ORIGINS: list = [
    "http://localhost:3000",
    "http://localhost:5000",
]
```

---

## Post-Deployment Checklist

### Backend Verification

- [ ] Health check passes: `curl https://<backend>/health`
- [ ] Search index stats visible in health response
- [ ] Test search endpoint: `POST /api/search` with sample query
- [ ] Test chat endpoint: `POST /api/chat` with sample question
- [ ] Admin endpoints protected (returns 403 without X-Admin-Key)
- [ ] PDF SAS URL generation works: `GET /api/pdf/<filename>.pdf`

### Frontend Verification

- [ ] App loads without errors
- [ ] Chat interface displays correctly
- [ ] Messages send and receive responses
- [ ] Policy citations display with links
- [ ] PDF viewer opens source documents
- [ ] Mobile responsive layout works

### Integration Verification

- [ ] CORS headers present in backend responses
- [ ] No mixed content warnings (HTTPS everywhere)
- [ ] Response times acceptable (<5s for queries)
- [ ] Error messages display gracefully

---

## Troubleshooting

### Backend Issues

| Issue | Solution |
|-------|----------|
| Container fails to start | Check logs: `az containerapp logs show --name policytech-backend --resource-group <RG>` |
| 503 Service Unavailable | Verify container health, check memory/CPU limits |
| Search returns empty | Verify index exists and has documents, check SEARCH_API_KEY |
| Chat returns 500 | Check AOAI credentials and deployment names |

### Frontend Issues

| Issue | Solution |
|-------|----------|
| Container fails to start | Check logs: `az containerapp logs show --name rush-policy-frontend --resource-group <RG>` |
| CORS errors | Verify frontend domain in backend Bicep `corsPolicy.allowedOrigins` |
| API calls fail | Verify BACKEND_URL environment variable, check network connectivity |
| Build fails | Verify Dockerfile, check Node.js version in container |

### Azure Issues

| Issue | Solution |
|-------|----------|
| ACR image pull fails | Check registry credentials, enable admin user on ACR |
| Secrets not resolving | Verify secret names match secretref values |
| Slow cold starts | Increase min replicas, use premium tier |

---

## Cost Optimization

### Development/Staging
- Use Azure App Service B1 tier (~$13/month)
- Single replica on Container Apps for both frontend and backend
- Consumption plan for low-traffic periods

### Production
- Azure Container Apps with autoscaling (2-10 replicas each)
- Azure AI Search Basic tier
- Azure OpenAI pay-per-token
- Custom domain with Azure-managed certificate

---

## Security Recommendations

1. **Secrets Management**
   - Use Azure Key Vault for production secrets
   - Never commit secrets to git
   - Rotate API keys periodically
   - Use Container Apps secrets with `secretRef`

2. **Network Security**
   - Enable HTTPS only (automatic on Azure Container Apps)
   - Configure Web Application Firewall (WAF) for production
   - Use Private Endpoints for Azure services in enterprise
   - Consider Azure Front Door for global load balancing

3. **Authentication**
   - Protect admin endpoints with strong API key
   - Consider Azure AD integration for internal users (Rush employees)
   - Implement rate limiting for public endpoints
   - Use Managed Identity for Azure service authentication

4. **Compliance (RUSH Healthcare)**
   - Ensure HIPAA compliance for any PHI
   - Enable audit logging via Application Insights
   - Configure log retention policies per RUSH requirements
