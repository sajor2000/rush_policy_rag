# RUSH Policy RAG Agent

Production-ready RAG (Retrieval-Augmented Generation) system for RUSH University System for Health policy retrieval.

| | |
|---|---|
| **Tech Stack** | FastAPI (Python 3.12) + Next.js 14 + Azure OpenAI |
| **Search** | vectorSemanticHybrid (Vector + BM25 + L2 Reranking) |
| **Deployment** | Azure Container Apps (Production Only) |
| **Current Version** | melissa-feedback-v1-hotfix2 (2026-01-08) |

---

## Deployment Team - Start Here

**Full step-by-step deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)**

### Quick Deploy Summary

```bash
# Step 1: Build Backend
cd apps/backend
az acr build --registry aiinnovation --image policytech-backend:latest .

# Step 2: Deploy Backend
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest

# Step 3: Build Frontend
cd apps/frontend
az acr build --registry aiinnovation --image policytech-frontend:latest .

# Step 4: Deploy Frontend
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest
```

### Production URLs

| Service | URL |
|---------|-----|
| **Frontend** | <https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io> |
| **Backend API** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io> |
| **Health Check** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/health> |
| **API Docs** | <https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io/docs> |

**Note**: This project uses a **production-only deployment model**. No staging or test environments are currently configured to minimize Azure costs. All testing should be performed locally before deploying to production.

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

## Local Development

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

## Project Structure

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
├── infrastructure/
│   ├── azure-container-app.bicep      # Backend Bicep template
│   └── azure-container-app-frontend.bicep  # Frontend Bicep template
├── DEPLOYMENT.md                      # Step-by-step deployment guide
├── CLAUDE.md                          # Development guidance
├── start_backend.sh                   # Backend launcher
└── start_frontend.sh                  # Frontend launcher
```

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
