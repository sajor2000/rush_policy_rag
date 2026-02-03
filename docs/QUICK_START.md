# Quick Start Guide

Get the RUSH Policy RAG Agent running locally in 30 minutes.

## Prerequisites

Before starting, ensure you have:

| Tool | Version | Check Command |
|------|---------|---------------|
| **Python** | 3.11+ | `python --version` |
| **Node.js** | 20+ | `node --version` |
| **Docker** | Latest | `docker --version` |
| **Azure CLI** | Latest | `az --version` |
| **Git** | Latest | `git --version` |

**Azure Access Required:**
- Azure subscription with access to `RU-Azure-NonProd`
- Credentials for Azure AI Search, Azure OpenAI, Azure Storage, Cohere Rerank

## Step 1: Clone and Setup (5 minutes)

```bash
# Clone the repository
git clone https://github.com/sajor2000/rush_policy_rag.git
cd rush_policy_rag

# Create environment file
cp .env.example .env
```

## Step 2: Configure Environment (10 minutes)

Edit `.env` with your Azure credentials. The minimum required variables are:

```bash
# Azure AI Search
SEARCH_ENDPOINT=https://policychataisearch.search.windows.net
SEARCH_API_KEY=<your-key>

# Azure OpenAI
AOAI_ENDPOINT=https://<your-aoai>.openai.azure.com/
AOAI_API_KEY=<your-key>
AOAI_CHAT_DEPLOYMENT=gpt-4.1
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Azure Storage
STORAGE_CONNECTION_STRING=<your-connection-string>
SOURCE_CONTAINER_NAME=policies-source
CONTAINER_NAME=policies-active

# Cohere Rerank
USE_COHERE_RERANK=true
COHERE_RERANK_ENDPOINT=https://<your-cohere>.models.ai.azure.com
COHERE_RERANK_API_KEY=<your-key>
```

**Get credentials from:** Azure Portal > Resource Group `RU-A-NonProd-AI-Innovation-RG`

## Step 3: Start Development Servers (5 minutes)

Open two terminal windows:

**Terminal 1 - Backend:**
```bash
./start_backend.sh
```
Wait for: `Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 - Frontend:**
```bash
./start_frontend.sh
```
Wait for: `Ready in X.Xs`

## Step 4: Verify It Works (5 minutes)

1. **Health Check:**
   ```bash
   curl http://localhost:8000/health
   ```
   Expected: `{"status":"healthy"}`

2. **Open UI:**
   - Navigate to http://localhost:3000
   - You should see the RUSH Policy Chat interface

3. **Test a Query:**
   - Type: "What is the code blue policy?"
   - You should receive a response with citations

## Step 5: First Deployment (5 minutes)

Deploy to Azure Container Apps:

```bash
# Login to Azure
az login
az account set --subscription "RU-Azure-NonProd"

# Login to Container Registry
az acr login --name aiinnovation

# Get current commit for tagging
TAG=$(git rev-parse --short HEAD)

# Build and push backend (linux/amd64 required)
docker build --platform linux/amd64 \
  -t aiinnovation.azurecr.io/rush-policy-backend:$TAG \
  -f apps/backend/Dockerfile apps/backend
docker push aiinnovation.azurecr.io/rush-policy-backend:$TAG

# Update backend Container App
az containerapp update \
  -n rush-policy-backend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-backend:$TAG

# Build and push frontend (linux/amd64 required)
docker build --platform linux/amd64 \
  -t aiinnovation.azurecr.io/rush-policy-frontend:$TAG \
  -f apps/frontend/Dockerfile apps/frontend
docker push aiinnovation.azurecr.io/rush-policy-frontend:$TAG

# Update frontend Container App
az containerapp update \
  -n rush-policy-frontend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-frontend:$TAG
```

**Live URLs:**
- Backend: https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io
- Frontend: https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Backend won't start | Check `.env` file exists and has valid credentials |
| "Connection refused" | Ensure backend is running before starting frontend |
| Docker build fails | Use `--platform linux/amd64` flag (required for Azure) |
| Azure login fails | Run `az login` and select correct subscription |
| No search results | Verify `SEARCH_ENDPOINT` and `SEARCH_API_KEY` are correct |

## Next Steps

| Task | Documentation |
|------|---------------|
| Understand the architecture | [RAG_DESIGN.md](RAG_DESIGN.md) |
| View architecture diagrams | [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) |
| Run tests | [TESTING.md](TESTING.md) |
| Full deployment guide | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Common issues | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Script reference | [SCRIPTS.md](SCRIPTS.md) |

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | AI assistant guide (streamlined project overview) |
| `apps/backend/main.py` | Backend entry point |
| `apps/frontend/src/app/page.tsx` | Frontend entry point |
| `apps/backend/app/services/chat_service.py` | Main RAG orchestrator |
| `apps/backend/policytech_prompt.txt` | RISEN prompt framework |
