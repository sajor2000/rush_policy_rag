# RUSH Policy RAG Agent

Production-ready RAG (Retrieval-Augmented Generation) system for RUSH University System for Health policy retrieval.

| | |
|---|---|
| **Tech Stack** | FastAPI (Python 3.12) + Next.js 14 + Azure OpenAI |
| **Search** | vectorSemanticHybrid (Vector + BM25 + L2 Reranking) |
| **Deployment** | Azure Container Apps (Non-Prod + Prod) |
| **Current Version** | melissa-feedback-v1-hotfix2 (2026-01-08) |

---

## New Engineer? Start Here

- Start-here onboarding: [docs/ONBOARDING.md](docs/ONBOARDING.md)
- Script map: [docs/SCRIPTS.md](docs/SCRIPTS.md)
- Environment variables: [docs/ENV_VARS.md](docs/ENV_VARS.md)
- Dev/Prod plan (per architecture PDF): [docs/DEV_PROD_PLAN.md](docs/DEV_PROD_PLAN.md)

## Deployment Team - Start Here

**Full step-by-step deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)**
**Deploy from IDE (VS Code): [docs/ONBOARDING.md](docs/ONBOARDING.md)**
**Dev/Prod environment plan: [docs/DEV_PROD_PLAN.md](docs/DEV_PROD_PLAN.md)**

### Deploy from Terminal (Non-Prod)

Targets **RU-Azure-NonProd** / `RU-A-NonProd-AI-Innovation-RG` per `docs/RushPolicyAssistant_Architecture.pdf`.

```bash
# Login + select subscription
az login
az account set --subscription "RU-Azure-NonProd"

# Step 1: Build Backend
cd apps/backend
az acr build --registry aiinnovation --image policytech-backend:latest .

# Step 2: Deploy Backend
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest

# Step 3: Get backend URL for frontend build
BACKEND_URL=$(az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query properties.configuration.ingress.fqdn -o tsv)

# Step 4: Build Frontend
cd ../frontend
az acr build --registry aiinnovation --image policytech-frontend:latest \
  --build-arg BACKEND_URL="https://$BACKEND_URL" .

# Step 5: Deploy Frontend
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest
```

### Deploy from Terminal (Prod)

Targets **RU-Azure-Prod** / `RU-A-Prod-AI-Innovation-RG`. Backend FQDN is TBD; use the CLI to fetch it.

```bash
az login
az account set --subscription "RU-Azure-Prod"

# Build backend
cd apps/backend
az acr build --registry aiinnovation --image policytech-backend:latest .

# Deploy backend
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-Prod-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest

# Get backend URL for frontend build (TBD in docs)
BACKEND_URL=$(az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-Prod-AI-Innovation-RG \
  --query properties.configuration.ingress.fqdn -o tsv)

# Build frontend
cd ../frontend
az acr build --registry aiinnovation --image policytech-frontend:latest \
  --build-arg BACKEND_URL="https://$BACKEND_URL" .

# Deploy frontend
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-Prod-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest
```

### Non-Prod URLs (RU-Azure-NonProd)

| Service | URL |
|---------|-----|
| **Frontend** | <https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io> |
| **Backend API** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io> |
| **Health Check** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health> |
| **API Docs** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/docs> |

### Prod URLs (RU-Azure-Prod)

| Service | URL |
|---------|-----|
| **Frontend** | <https://policychat.rush.edu> |
| **Backend API** | TBD (Azure Container Apps default hostname; see `docs/DEV_PROD_PLAN.md`) |

**Note**: Non-Prod and Prod live in separate subscriptions. There is no dedicated staging environment.

---

## What Gets Deployed

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   User Browser  │────▶│  Next.js        │────▶│  FastAPI        │
│                 │     │  Frontend       │     │  Backend        │
│                 │     │  Port 3000      │     │  Port 8000      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                       │
                  ┌────────────────────────────────────┼─────────────────────────┐
                  │                │                   │                │        │
                  ▼                ▼                   ▼                ▼        │
      ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐ │
      │ Azure AI      │  │ Azure OpenAI  │  │ Cohere Rerank │  │ Azure Blob    │ │
      │ Search        │  │ ───────────── │  │ ───────────── │  │ Storage       │ │
      │ ───────────── │  │ GPT-4.1       │  │ rerank-v3-5   │  │ ───────────── │ │
      │ rush-policies │  │ embeddings    │  │ cross-encoder │  │ PDFs          │ │
      │ 3072-dim      │  │ (3-large)     │  │ AI Foundry    │  │               │ │
      └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘ │
                                                                                  │
      ◄──────────────────────── RAG PIPELINE ────────────────────────────────────►
      1. Hybrid Search (Vector + BM25)  2. Cohere Rerank  3. GPT-4.1 Generation
```

**Two containers only:**
- `rush-policy-backend` (FastAPI Python)
- `rush-policy-frontend` (Next.js Node.js)

**NO Azure Functions. NO Redis. NO Serverless.**

**AI Services:**
- Azure OpenAI (GPT-4.1 chat + embeddings)
- Cohere Rerank 3.5 (cross-encoder for negation-aware retrieval)

---

## Get Started (Local)

### Prerequisites

- Python 3.9+
- Node.js 18+
- Azure AI Search service with `rush-policies` index
- Azure OpenAI service (GPT-4.1 + text-embedding-3-large)
- Azure Blob Storage account

### Environment Setup

```bash
cp .env.example .env
# Edit .env with your Azure credentials
```

Required variables:
```bash
# Azure AI Search
SEARCH_ENDPOINT=https://<search>.search.windows.net
SEARCH_API_KEY=<key>

# Azure OpenAI
AOAI_ENDPOINT=https://<openai>.openai.azure.com/
AOAI_API_KEY=<key>
AOAI_CHAT_DEPLOYMENT=gpt-4.1
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Azure Blob Storage
STORAGE_CONNECTION_STRING=<connection_string>

# Enable On Your Data (vectorSemanticHybrid)
USE_ON_YOUR_DATA=true

# Cohere Rerank 3.5 (cross-encoder reranking)
USE_COHERE_RERANK=true
COHERE_RERANK_ENDPOINT=https://<cohere-endpoint>.models.ai.azure.com
COHERE_RERANK_API_KEY=<key>
COHERE_RERANK_MODEL=cohere-rerank-v3-5
```

### Start Services

```bash
# Terminal 1: Backend
./start_backend.sh

# Terminal 2: Frontend
./start_frontend.sh
```

Open http://localhost:3000 and ask a policy question.

---

## Final Folder Structure

```
rag_pt_rush/
├── apps/
│   ├── backend/                       # FastAPI backend
│   │   ├── main.py                    # API entrypoint
│   │   ├── Dockerfile                 # Container build
│   │   ├── app/
│   │   │   ├── services/
│   │   │   │   ├── on_your_data_service.py  # Azure OpenAI "On Your Data"
│   │   │   │   ├── chat_service.py          # Chat orchestration
│   │   │   │   ├── cohere_rerank_service.py # Cohere Rerank 3.5
│   │   │   │   └── synonym_service.py       # Query expansion
│   │   │   └── api/routes/            # API endpoints
│   │   ├── azure_policy_index.py      # Search index management
│   │   └── preprocessing/chunker.py   # PDF processing
│   └── frontend/                      # Next.js 14 app
│       ├── Dockerfile                 # Container build
│       ├── src/app/                   # App Router
│       └── src/components/            # UI components
├── docs/
│   ├── RushPolicyAssistant_Architecture.pdf
│   ├── ONBOARDING.md
│   ├── DEV_PROD_PLAN.md
│   ├── SCRIPTS.md
│   └── ... (other docs)
├── scripts/                            # Utilities, eval, and deploy helpers
│   └── deploy/                         # Azure deployment scripts
├── infrastructure/
│   ├── azure-container-app.bicep      # Backend Bicep template
│   └── azure-container-app-frontend.bicep  # Frontend Bicep template
├── .env.example                        # Env template (copy to .env locally)
├── DEPLOYMENT.md                      # Step-by-step deployment guide
├── CLAUDE.md                          # Development guidance
├── start_backend.sh                   # Backend launcher
└── start_frontend.sh                  # Frontend launcher
```

---

## App Entry Points

- Backend: `apps/backend/main.py` (FastAPI app instance `app`)
- Frontend: `apps/frontend/src/app/page.tsx` (main UI entry point)

---

## Local Folders vs Git-Tracked Files

**Local-only (gitignored)** — do not commit:
- `.env`, `apps/frontend/.env.local`
- `.venv/`, `__pycache__/`, `.pytest_cache/`
- `node_modules/`, `.next/`
- `apps/backend/data/`, `data/`
- `reports/`, `test_results*.json`, `*_evaluation_results.json`, `coverage/`
- `.azure/`, `.vscode/`, `.idea/`, `.DS_Store`

**Git-tracked** — keep in repo:
- `apps/`, `docs/`, `scripts/`, `infrastructure/`
- `README.md`, `DEPLOYMENT.md`, `CLAUDE.md`
- `.env.example`, `docker-compose.yml`, `package.json`

See `.gitignore` for the full list.

---

## Environments (from `docs/RushPolicyAssistant_Architecture.pdf`)

- **Non-Prod**: RU-Azure-NonProd → `RU-A-NonProd-AI-Innovation-RG`
- **Prod**: RU-Azure-Prod → `RU-A-Prod-AI-Innovation-RG`
- **Dev domain**: Azure Container Apps default hostname
- **Prod domain**: `policychat.rush.edu`

See `docs/DEV_PROD_PLAN.md` for details and the dev → prod promotion flow.

---

## Azure Services Required

| Azure Service | Purpose | Required |
|---------------|---------|----------|
| **Azure AI Search** | Vector store + semantic ranking | Yes |
| **Azure OpenAI** | GPT-4.1 + embeddings | Yes |
| **Azure AI Foundry (Cohere)** | Cohere Rerank 3.5 deployment | Yes |
| **Azure Blob Storage** | PDF document storage | Yes |
| **Azure Container Apps** | Host frontend + backend | Yes |
| **Azure Container Registry** | Store container images | Yes |

---

## Key Features

### Core RAG Pipeline

- **Azure OpenAI "On Your Data"**: vectorSemanticHybrid search (Vector + BM25 + L2 Reranking)
- **Cohere Rerank 3.5**: Cross-encoder reranking via Azure AI Foundry for negation-aware retrieval
- **Production Security**: Rate limiting, input validation, CSP headers
- **PDF Upload & Viewing**: End-to-end pipeline with async blob storage
- **1,800+ Document Support**: top_k=50 with semantic ranker optimization
- **Healthcare Synonyms**: 132 synonym rules for medical terminology

### Recent Enhancements (melissa-feedback-v1, 2026-01-08)

- **Device Ambiguity Detection**: Intelligent clarification UI for ambiguous medical device queries (IV, catheter, line, port)
- **Three-Tier PDF Access**: Quick access buttons on each evidence card + sticky panel + bottom section
- **Score Windowing**: Post-rerank filtering (60% threshold) reduces irrelevant results by 60-70%
- **Collapsible Related Evidence**: Prevents users from following incorrect policies
- **Context-Aware Synonym Expansion**: Priority-based stopping prevents cascading query noise

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/chat` | POST | Policy Q&A (main endpoint) |
| `/api/chat/stream` | POST | Streaming chat responses |
| `/api/search` | POST | Direct Azure AI Search |
| `/api/search-instances` | POST | Within-policy search |
| `/api/pdf/{filename}` | GET | PDF SAS URL generation |
| `/api/admin/index-stats` | GET | Index statistics (protected) |
| `/api/admin/cache/stats` | GET | Cache statistics (protected) |
| `/docs` | GET | Swagger API documentation |

---

## License

Internal use only - Rush University System for Health

## Support

- **Deployment Guide**: [DEPLOYMENT.md](DEPLOYMENT.md)
- **Development Guide**: [CLAUDE.md](CLAUDE.md)
- **Production Deployment Summary**: [docs/deployment-completion-summary.md](docs/deployment-completion-summary.md)
- **Changelog**: [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **Policy Admin**: https://rushumc.navexone.com/
