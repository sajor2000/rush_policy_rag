# RUSH Policy RAG Agent

Production-ready RAG (Retrieval-Augmented Generation) system for RUSH University System for Health policy retrieval.

| | |
|---|---|
| **Tech Stack** | FastAPI (Python 3.12) + Next.js 16 + Azure OpenAI |
| **Search** | vectorSemanticHybrid (Vector + BM25 + L2 Reranking) |
| **Deployment** | Azure Container Apps (Non-Prod + Prod) |
| **Current Version** | melissa-feedback-v1-hotfix2 (2026-01-08) |

---

## New Engineer? Start Here

**Get running in 30 minutes:** [docs/QUICK_START.md](docs/QUICK_START.md)

| I want to... | Go to |
|--------------|-------|
| Get running quickly | [QUICK_START.md](docs/QUICK_START.md) |
| Understand RAG architecture | [RAG_DESIGN.md](docs/RAG_DESIGN.md) |
| See architecture diagrams | [ARCHITECTURE_DIAGRAMS.md](docs/ARCHITECTURE_DIAGRAMS.md) |
| Full onboarding guide | [ONBOARDING.md](docs/ONBOARDING.md) |
| Script reference | [SCRIPTS.md](docs/SCRIPTS.md) |
| Environment variables | [ENV_VARS.md](docs/ENV_VARS.md) |
| Deploy to Azure | [DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Security & compliance | [SECURITY.md](SECURITY.md) |
| Audit logging & quality | [AUDIT_AND_QUALITY.md](docs/AUDIT_AND_QUALITY.md) |
| PolicyTech document sourcing | [POLICYTECH_DOCUMENT_SOURCING.md](docs/POLICYTECH_DOCUMENT_SOURCING.md) |
| Run tests | [TESTING.md](docs/TESTING.md) |
| Contribute | [CONTRIBUTING.md](docs/CONTRIBUTING.md) |

## Deployment Team - Start Here

**Full step-by-step deployment guide: [DEPLOYMENT.md](docs/DEPLOYMENT.md)**
**Deploy from IDE (VS Code): [docs/ONBOARDING.md](docs/ONBOARDING.md)**

### Deploy from Terminal (Non-Prod)

Targets **RU-Azure-NonProd** / `RU-A-NonProd-AI-Innovation-RG` per `docs/RushPolicyAssistant_Architecture.pdf`.

**CRITICAL**: Always use `az acr build` for remote builds — no local Docker required. Builds run on Azure's infrastructure (linux/amd64) and push to ACR automatically.

```bash
# Login + select subscription
az login
az account set --subscription "RU-Azure-NonProd"

# Get current commit for tagging
TAG=$(git rev-parse --short HEAD)

# Step 1: Build backend (remote build on ACR)
az acr build --registry aiinnovation --platform linux/amd64 \
  --image rush-policy-backend:$TAG --file apps/backend/Dockerfile apps/backend

# Step 2: Deploy Backend
az containerapp update \
  -n rush-policy-backend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-backend:$TAG

# Step 3: Build frontend (remote build on ACR)
az acr build --registry aiinnovation --platform linux/amd64 \
  --image rush-policy-frontend:$TAG --file apps/frontend/Dockerfile apps/frontend

# Step 4: Deploy Frontend
az containerapp update \
  -n rush-policy-frontend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-frontend:$TAG
```

### Deploy from Terminal (Prod)

Targets **RU-Azure-Prod** / `RU-A-Prod-AI-Innovation-RG`.

**CRITICAL**: Always use `az acr build` for remote builds — no local Docker required.

```bash
az login
az account set --subscription "RU-Azure-Prod"

# Get current commit for tagging
TAG=$(git rev-parse --short HEAD)

# Build and deploy backend
az acr build --registry aiinnovation --platform linux/amd64 \
  --image rush-policy-backend:$TAG --file apps/backend/Dockerfile apps/backend
az containerapp update \
  -n rush-policy-backend \
  -g RU-A-Prod-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-backend:$TAG

# Build and deploy frontend
az acr build --registry aiinnovation --platform linux/amd64 \
  --image rush-policy-frontend:$TAG --file apps/frontend/Dockerfile apps/frontend
az containerapp update \
  -n rush-policy-frontend \
  -g RU-A-Prod-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-frontend:$TAG
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
| **Backend API** | TBD (Azure Container Apps default hostname) |

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
      │ ───────────── │  │ GPT-4.1       │  │ rerank-v4.0   │  │ ───────────── │ │
      │ rush-policies-│  │ embeddings    │  │ Pro (cross-   │  │ PDFs          │ │
      │ active alias  │  │              │  │ encoder)      │  │               │ │
      │ 3072-dim      │  │ (3-large)     │  │ encoder)      │  │               │ │
      └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘ │
                                                                                  │
      ◄──────────────────────── RAG PIPELINE ────────────────────────────────────►
      1. Hybrid Search (Vector + BM25)  2. Cohere Rerank 4.0 Pro  3. GPT-4.1 Generation
```

**Two containers only:**
- `rush-policy-backend` (FastAPI Python)
- `rush-policy-frontend` (Next.js Node.js)

**NO Azure Functions. NO Redis. NO Serverless.**

**AI Services:**
- Azure OpenAI (GPT-4.1 chat + embeddings)
- Cohere Rerank 4.0 Pro (cross-encoder for negation-aware retrieval, 9.5% accuracy improvement over v3.5)

---

## Get Started (Local)

### Prerequisites

- Python 3.9+
- Node.js 18+
- Azure AI Search service with `rush-policies-active` alias
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

# Cohere Rerank 4.0 Pro (cross-encoder reranking - Dec 2025 release)
# 9.5% accuracy improvement over v3.5, 32k context, healthcare-optimized
USE_COHERE_RERANK=true
COHERE_RERANK_ENDPOINT=https://<cohere-endpoint>.models.ai.azure.com
COHERE_RERANK_API_KEY=<key>
COHERE_RERANK_MODEL=Cohere-rerank-v4.0-pro
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

## Folder Structure

```
rag_pt_rush/
├── apps/
│   ├── backend/                       # FastAPI backend (:8000)
│   │   ├── main.py                    # API entrypoint
│   │   ├── Dockerfile                 # Container build
│   │   ├── app/
│   │   │   ├── api/routes/            # chat.py, search.py, pdf.py, admin.py
│   │   │   ├── core/                  # config, auth, security, rate_limit
│   │   │   ├── services/              # 24 services (RAG pipeline)
│   │   │   ├── models/                # schemas.py, audit_schemas.py
│   │   │   └── evaluation/            # DeepEval metrics, diagnostics
│   │   ├── preprocessing/             # PolicyChunker (Docling + PyMuPDF)
│   │   ├── azure_policy_index.py      # Search index management
│   │   └── scripts/                   # Backend-specific scripts
│   └── frontend/                      # Next.js 16 app (:3000)
│       ├── Dockerfile                 # Container build
│       ├── src/app/                   # App Router + API route handlers
│       ├── src/components/            # UI components (Radix-based)
│       └── src/lib/                   # API client, sanitization, formatting
├── docs/                              # All documentation
│   ├── DEPLOYMENT.md                  # Step-by-step Azure deployment
│   ├── QUICK_START.md                 # 30-minute setup guide
│   └── ... (20+ docs)
├── scripts/                           # Ingestion, evaluation, deployment
│   └── deploy/                        # Azure deployment scripts
├── infrastructure/                    # Bicep templates + config
├── tests/                             # RAG accuracy + PromptFoo compliance
├── CLAUDE.md                          # AI assistant instructions
├── README.md                          # This file
├── SECURITY.md                        # Vulnerability reporting
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
- `README.md`, `CLAUDE.md`
- `.env.example`, `docker-compose.yml`, `package.json`

See `.gitignore` for the full list.

---

## Environments (from `docs/RushPolicyAssistant_Architecture.pdf`)

- **Non-Prod**: RU-Azure-NonProd → `RU-A-NonProd-AI-Innovation-RG`
- **Prod**: RU-Azure-Prod → `RU-A-Prod-AI-Innovation-RG`
- **Dev domain**: Azure Container Apps default hostname
- **Prod domain**: `policychat.rush.edu`

---

## Azure Services Required

| Azure Service | Purpose | Required |
|---------------|---------|----------|
| **Azure AI Search** | Vector store + semantic ranking | Yes |
| **Azure OpenAI** | GPT-4.1 + embeddings | Yes |
| **Azure AI Foundry (Cohere)** | Cohere Rerank 4.0 Pro deployment | Yes |
| **Azure Blob Storage** | PDF document storage | Yes |
| **Azure Container Apps** | Host frontend + backend | Yes |
| **Azure Container Registry** | Store container images | Yes |

---

## Key Features

### Core RAG Pipeline

- **Azure OpenAI "On Your Data"**: vectorSemanticHybrid search (Vector + BM25 + L2 Reranking)
- **Cohere Rerank 4.0 Pro**: Cross-encoder reranking via Azure AI Foundry for negation-aware retrieval (9.5% accuracy improvement, 32k context window)
- **Production Security**: Rate limiting, input validation, CSP headers
- **PDF Upload & Viewing**: End-to-end pipeline with async blob storage
- **1,800+ Document Support**: top_k=50 with semantic ranker optimization
- **Healthcare Synonyms**: 132 synonym rules for medical terminology


## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and full security documentation.

**Key controls implemented:**

- **Authentication**: Azure AD (JWT) with tenant/audience verification
- **Input Validation**: OData injection prevention, prompt injection defense (unicode normalization, Cyrillic homoglyph mapping)
- **XSS Protection**: DOMPurify sanitization on all LLM-rendered content
- **Rate Limiting**: Per-IP rate limiting (30 req/min default)
- **Resilience**: Circuit breaker pattern for Azure OpenAI outages
- **Static Analysis**: Semgrep (10 custom rules), Bandit, CodeQL, pip-audit, npm audit
- **Security Headers**: CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- **HIPAA Awareness**: No ePHI storage; chat audit logs truncated with 90-day retention

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/health/detailed` | GET | Detailed diagnostics (protected) |
| `/api/chat` | POST | Policy Q&A (main endpoint) |
| `/api/chat/stream` | POST | Streaming chat responses |
| `/api/search` | POST | Direct Azure AI Search |
| `/api/search-instances` | POST | Within-policy instance search |
| `/api/search-within-policy` | POST | Search within a specific policy |
| `/api/pdf/{filename}` | GET | PDF SAS URL generation |
| `/api/sync-info` | GET | Latest policy sync date |
| `/api/admin/index-stats` | GET | Index statistics (protected) |
| `/api/admin/audit/*` | GET | Audit log access (protected) |
| `/api/admin/cache/*` | GET/POST | Cache stats, invalidation, toggle (protected) |
| `/docs` | GET | Swagger API documentation |

---

## License

Internal use only - Rush University System for Health

## Support

- **Deployment Guide**: [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **Development Guide**: [CLAUDE.md](CLAUDE.md)
- **Changelog**: [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **Policy Admin**: https://rushumc.navexone.com/
