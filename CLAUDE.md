# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RUSH Policy RAG Agent - A production-ready RAG (Retrieval-Augmented Generation) system for policy retrieval at Rush University System for Health.

**Tech Stack**: FastAPI backend + Next.js 14 frontend + Azure OpenAI "On Your Data" (vectorSemanticHybrid) + Docling/PyMuPDF PDF processing

**Architecture Status**: ✅ COMPLETED - Single backend (FastAPI + Next.js only, no Azure Functions)

## Azure Services Required

This application **requires** the following Azure services to function:

| Azure Service | Purpose | Required |
|---------------|---------|----------|
| **Azure AI Search** | Vector store (3072-dim embeddings) + semantic ranking + 132 synonym rules | ✅ **Required** |
| **Azure OpenAI** | GPT-4.1 (chat) + text-embedding-3-large (embeddings) | ✅ **Required** |
| **Azure AI Foundry (Cohere)** | Cohere Rerank 3.5 cross-encoder deployment | ✅ **Required** |
| **Azure Blob Storage** | PDF document storage (3 containers: source, active, archive) | ✅ **Required** |
| **Azure Container Apps** | Host FastAPI backend + Next.js frontend | ✅ **Required** |
| **Azure AD** | Authentication for production | Optional |

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step Azure resource creation commands.

## Azure Infrastructure (Deployed)

**IMPORTANT**: All resources are deployed in the following location:

| Setting | Value |
|---------|-------|
| **Subscription** | `RU-Azure-NonProd` (ID: `e5282183-61c9-4c17-a58a-9442db9594d5`) |
| **Resource Group** | `RU-A-NonProd-AI-Innovation-RG` |
| **Location** | `eastus` |

**Deployed Resources:**

| Resource | Name | Type |
|----------|------|------|
| Container Apps Environment | `rush-policy-env-production` | Microsoft.App/managedEnvironments |
| Backend Container App | `rush-policy-backend` | Microsoft.App/containerApps |
| Frontend Container App | `rush-policy-frontend` | Microsoft.App/containerApps |
| Container Registry | `aiinnovation` | Microsoft.ContainerRegistry/registries |
| AI Search | `policychataisearch` | Microsoft.Search/searchServices |
| Blob Storage | `policytechrush` | Microsoft.Storage/storageAccounts |
| Cognitive Services | `rua-nonprod-ai-innovation` | Microsoft.CognitiveServices/accounts |

**Live URLs:**
- Backend: `https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`
- Frontend: `https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`

**Before deploying**, ensure you're logged into the correct subscription:
```bash
az account set --subscription "RU-Azure-NonProd"
az account show  # Verify: should show "RU-Azure-NonProd"
```

## End-to-End Data Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DOCUMENT INGESTION PIPELINE                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. PDF UPLOAD TO AZURE BLOB STORAGE                                        │
│     ┌─────────────────┐                                                     │
│     │ policies-source │ ← Upload PDFs here (staging area)                   │
│     └────────┬────────┘                                                     │
│              │                                                              │
│  2. PDF PROCESSING (Docling + PyMuPDF)                                     │
│              ▼                                                              │
│     ┌─────────────────────────────────────────────────────┐                 │
│     │  preprocessing/chunker.py (PolicyChunker)           │                 │
│     │  ├── PyMuPDF checkbox extraction (primary)          │                 │
│     │  ├── Docling TableFormer ACCURATE (fallback)        │                 │
│     │  ├── Metadata extraction (title, ref#, owner, date) │                 │
│     │  ├── 9 entity boolean filters (RUMC, RUMG, RMG...)  │                 │
│     │  └── Hierarchical chunking (~1,500 chars)           │                 │
│     └────────┬────────────────────────────────────────────┘                 │
│              │                                                              │
│  3. AZURE AI SEARCH VECTOR STORE                                            │
│              ▼                                                              │
│     ┌─────────────────────────────────────────────────────┐                 │
│     │  Azure AI Search Index: rush-policies               │                 │
│     │  ├── 29-field schema                                │                 │
│     │  ├── content_vector (3072-dim, text-embedding-3-large) │             │
│     │  ├── Entity boolean filters (O(1) filtering)        │                 │
│     │  └── Semantic ranker with L2 reranking              │                 │
│     └────────┬────────────────────────────────────────────┘                 │
│              │                                                              │
│  4. COPY TO PRODUCTION                                                      │
│              ▼                                                              │
│     ┌─────────────────┐                                                     │
│     │ policies-active │ ← Production storage (auto-synced)                  │
│     └─────────────────┘                                                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUERY PIPELINE (RAG)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Browser                                                                │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Next.js 14 Frontend (:3000)                                        │    │
│  │  ├── RUSH brand styling, metadata display                           │    │
│  │  ├── Security Headers (CSP, HSTS, X-Frame-Options)                  │    │
│  │  └── API Proxy Routes → FastAPI                                     │    │
│  └────────┬────────────────────────────────────────────────────────────┘    │
│           │ POST /api/chat                                                   │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  FastAPI Backend (:8000)                                            │    │
│  │  ├── Rate Limiting (slowapi - 30/min)                               │    │
│  │  ├── Input Validation (OData injection prevention)                  │    │
│  │  ├── Azure AD Authentication (optional via REQUIRE_AAD_AUTH)        │    │
│  │  └── Async I/O (azure.storage.blob.aio)                             │    │
│  └────────┬────────────────────────────────────────────────────────────┘    │
│           │                                                                  │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────┐                │
│  │  Synonym Expansion (SynonymService)                     │                │
│  │  ├── 155 medical abbreviations (ED → emergency dept)    │                │
│  │  ├── 56 misspelling corrections (cathater → catheter)   │                │
│  │  ├── Hospital codes (code blue → cardiac arrest)        │                │
│  │  └── Rush terms (RUMC → Rush University Medical Center) │                │
│  └────────┬────────────────────────────────────────────────┘                │
│           │                                                                  │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────┐                │
│  │  Azure OpenAI "On Your Data" (OnYourDataService)        │                │
│  │  ├── Model: GPT-4.1 (Chat Completions API)              │                │
│  │  └── vectorSemanticHybrid Search                        │                │
│  │       ├── Vector search (text-embedding-3-large)        │                │
│  │       ├── BM25 + 132 synonym rules (keyword match)      │                │
│  │       ├── L2 Semantic Reranking (best quality)          │                │
│  │       └── semantic_configuration for ranking            │                │
│  └────────┬────────────────────────────────────────────────┘                │
│           │                                                                  │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────┐                │
│  │  Cohere Rerank 3.5 (CohereRerankService)                │                │
│  │  ├── Cross-encoder reranking (negation-aware)           │                │
│  │  ├── Azure AI Foundry serverless deployment             │                │
│  │  ├── Top N: 10 docs retained after rerank               │                │
│  │  └── Min Score: 0.15 (healthcare-calibrated)            │                │
│  └────────┬────────────────────────────────────────────────┘                │
│           │                                                                  │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────┐                │
│  │  Response with Citations                                │                │
│  │  ├── Quick Answer                                       │                │
│  │  ├── Policy Reference (title, ref#, section, date)     │                │
│  │  └── Entity applicability (RUMC, RUMG, etc.)           │                │
│  └─────────────────────────────────────────────────────────┘                │
│                                                                              │
│  NO AZURE FUNCTIONS - Pure FastAPI + Next.js                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Commands

### Frontend (from `/apps/frontend/`)
```bash
npm run dev      # Development server with hot reload (port 3000)
npm run build    # Production build
npm start        # Start production server
npm run check    # TypeScript type checking
```

### Backend (from `/apps/backend/`)
```bash
python main.py                                    # Run development server
uvicorn main:app --reload --port 8000            # Alternative with uvicorn
pip install -r requirements.txt                   # Install dependencies
```

### Quick Start Scripts (from root)
```bash
./start_backend.sh   # Creates venv, installs deps, runs backend
./start_frontend.sh  # Installs deps if needed, runs frontend
```

### Document Ingestion (from `/apps/backend/`)
```bash
# Full pipeline ingestion (recommended - 50 docs in ~85-113s)
python scripts/full_pipeline_ingest.py

# Legacy ingestion from Azure Blob Storage
python scripts/ingest_all_policies.py

# Validate parsing only (no upload to search index)
python scripts/ingest_all_policies.py --validate-only --sample 20

# Process local folder of PDFs
python scripts/ingest_all_policies.py --local-folder ./data/policies

# Check sync status between source and active containers
python policy_sync.py detect

# Run incremental sync (only changed documents)
python policy_sync.py sync
```

### PDF Blob Storage (from root) - Required for PDF Viewing Feature
```bash
# Upload all PDFs from default location (apps/backend/data/test_pdfs/)
python scripts/upload_pdfs_to_blob.py

# Upload from a specific directory
python scripts/upload_pdfs_to_blob.py /path/to/pdfs

# Upload a single file
python scripts/upload_pdfs_to_blob.py /path/to/policy.pdf

# List existing blobs in policies-active container
python scripts/upload_pdfs_to_blob.py --list
```

**Important**: The PDF viewing feature in the frontend requires PDFs to be uploaded to Azure Blob Storage (`policies-active` container). Without this, clicking "View PDF" in search results will fail with a 404 error.

### Synonym Management (from `/apps/backend/`)
```bash
# Update Azure AI Search synonym map (132 healthcare rules)
python azure_policy_index.py synonyms

# Test synonym expansion + search for a query
python azure_policy_index.py test-synonyms "ED code blue policy"

# Run synonym service unit tests
python -m pytest tests/test_synonym_service.py -v
```

### Testing & Evaluation (from root)
```bash
# Run full RAG test suite (36 test cases, 100% pass rate with Cohere Rerank)
python scripts/run_test_dataset.py

# Run ENHANCED evaluation (60 tests: Cohere, hallucination, RISEN compliance)
python scripts/run_enhanced_evaluation.py

# Run only Cohere negation tests
python scripts/run_enhanced_evaluation.py --category cohere_negation

# Run only critical safety tests
python scripts/run_enhanced_evaluation.py --criticality critical

# Run hallucination prevention tests
python scripts/run_enhanced_evaluation.py --category halluc

# Run RISEN rule compliance tests
python scripts/run_enhanced_evaluation.py --category risen_

# View test results summary
cat test_results.json | jq '.summary'
cat enhanced_evaluation_results.json | jq '.report.summary'

# Debug PDF checkbox extraction
python scripts/debug_pdf_structure.py

# A/B test checkbox extraction methods
python scripts/test_checkbox_extraction.py
```

### Test Dataset Categories

| Category | Tests | Purpose |
|----------|-------|---------|
| `cohere_negation` | 8 | Cross-encoder negation understanding |
| `cohere_contradiction` | 4 | Premise contradiction detection |
| `hallucination_fabrication` | 5 | Prevent inventing non-existent policies |
| `hallucination_extrapolation` | 3 | Prevent speculation beyond source text |
| `risen_role` | 4 | RAG-only, no opinions, RUSH-only |
| `risen_citation` | 3 | Mandatory citation compliance |
| `risen_refusal` | 3 | Safety bypass refusal |
| `risen_adversarial` | 5 | Jailbreak/prompt injection resistance |
| `risen_unclear` | 4 | Gibberish/typo handling |
| `safety_critical` | 4 | Life-safety accuracy (phone numbers, thresholds) |
| `verbatim_accuracy` | 4 | Exact numbers/timeframes |

## Project Structure

```
rag_pt_rush/
├── apps/
│   ├── backend/                              # FastAPI backend
│   │   ├── main.py                           # API entrypoint with middleware stack
│   │   ├── app/
│   │   │   ├── api/routes/                   # API endpoints
│   │   │   │   ├── chat.py                   # /api/chat, /api/chat/stream
│   │   │   │   ├── search.py                 # /api/search-instances
│   │   │   │   ├── pdf.py                    # /api/pdf/{filename}
│   │   │   │   └── admin.py                  # /api/admin/* (protected)
│   │   │   ├── core/                         # Cross-cutting concerns
│   │   │   │   ├── config.py                 # Pydantic settings
│   │   │   │   ├── auth.py                   # Azure AD JWT validation
│   │   │   │   ├── security.py               # Input validation, OData injection prevention
│   │   │   │   ├── rate_limit.py             # slowapi (30/min per IP)
│   │   │   │   ├── circuit_breaker.py        # pybreaker for Azure services
│   │   │   │   └── logging_middleware.py     # Structured request logging
│   │   │   ├── services/                     # Business logic (see Services below)
│   │   │   ├── models/                       # Pydantic request/response schemas
│   │   │   └── dependencies.py               # FastAPI dependency injection
│   │   ├── preprocessing/                    # PDF processing module
│   │   │   ├── __init__.py                   # Exports: PolicyChunker, PolicyChunk
│   │   │   ├── chunker.py                    # Main chunker (Docling + PyMuPDF)
│   │   │   ├── policy_chunk.py               # PolicyChunk dataclass
│   │   │   ├── rush_metadata.py              # ProcessingStatus enum, constants
│   │   │   ├── checkbox_extractor.py         # "Applies To" checkbox detection
│   │   │   ├── metadata_extractor.py         # Title, ref#, date extraction
│   │   │   └── archive/                      # Legacy code (deprecated)
│   │   ├── azure_policy_index.py             # Azure AI Search client
│   │   ├── pdf_service.py                    # SAS URL generation for PDFs
│   │   ├── policy_sync.py                    # Differential sync (SHA-256)
│   │   └── policytech_prompt.txt             # RISEN prompt framework
│   │
│   └── frontend/                             # Next.js 14 application
│       ├── src/
│       │   ├── app/                          # App Router pages
│       │   │   ├── page.tsx                  # Main chat interface
│       │   │   ├── layout.tsx                # Root layout with providers
│       │   │   └── api/                      # Route handlers (proxy to backend)
│       │   ├── components/                   # React components
│       │   │   ├── ChatInterface.tsx         # Main chat container
│       │   │   ├── ChatMessage.tsx           # Message rendering
│       │   │   ├── chat/                     # Chat sub-components
│       │   │   │   ├── index.ts              # Barrel exports
│       │   │   │   └── FormattedQuickAnswer.tsx
│       │   │   └── ...                       # Other UI components
│       │   └── lib/                          # Utilities
│       │       ├── api.ts                    # Backend API client
│       │       ├── chatMessageFormatting.ts  # Citation parsing utilities
│       │       ├── constants.ts              # App-wide constants
│       │       └── utils.ts                  # cn() and helpers
│       └── next.config.js                    # Security headers (CSP, HSTS)
│
├── scripts/
│   ├── deploy/                               # Azure deployment scripts
│   └── upload_pdfs_to_blob.py                # PDF upload utility
├── docs/
│   ├── TECHNICAL_ARCHITECTURE_PWC.md         # Architecture overview for review
│   ├── DEPLOYMENT.md                         # Step-by-step deployment guide
│   └── CHANGELOG.md                          # Release notes
├── semantic-search-synonyms.json             # 24 synonym groups, 155+ rules
├── start_backend.sh                          # Backend launcher
├── start_frontend.sh                         # Frontend launcher
└── .env                                      # Environment variables (not in git)
```

### Backend Services (`apps/backend/app/services/`)

| Service | File | Purpose |
|---------|------|---------|
| **ChatService** | `chat_service.py` | Main RAG orchestrator |
| **OnYourDataService** | `on_your_data_service.py` | Azure OpenAI "On Your Data" integration |
| **CohereRerankService** | `cohere_rerank_service.py` | Cross-encoder reranking (negation-aware) |
| **SynonymService** | `synonym_service.py` | Query-time synonym expansion (1MB JSON) |
| **CacheService** | `cache_service.py` | Multi-layer in-memory caching |
| **ChatAuditService** | `chat_audit_service.py` | Query logging to blob storage |
| **InstanceSearchService** | `instance_search_service.py` | Within-policy search |

**Query Processing Modules** (extracted from chat_service.py):
| Module | Purpose |
|--------|---------|
| `query_processor.py` | Intent detection, policy resolution |
| `query_validation.py` | Input validation, entity extraction |
| `query_enhancer.py` | Query expansion and refinement |
| `confidence_calculator.py` | Response confidence scoring |
| `device_disambiguator.py` | Medical device term disambiguation |
| `entity_ranking.py` | Location-based policy prioritization |
| `ranking_utils.py` | Scoring utilities |
| `response_formatter.py` | Output formatting |

**Search Infrastructure**:
| Module | Purpose |
|--------|---------|
| `search_result.py` | SearchResult dataclass |
| `search_synonyms.py` | SYNONYMS constant for Azure AI Search |

## Key Technical Details

### PDF Processing (Docling + PyMuPDF)

The chunker (`preprocessing/chunker.py`) uses a dual-library approach:

**PyMuPDF (Primary for Checkboxes)**
- Raw text extraction from first page for "Applies To" checkboxes
- Handles checkbox patterns: `☒`, `☐`, `✓`, `✔`, `■`
- Fixes Docling's TableFormer truncation issue with checkbox rows
- Implementation: `_extract_applies_to_from_raw_pdf()` (lines 687-750)

**Docling (Fallback & Content)**
- **TableFormer ACCURATE mode**: Superior table extraction from policy headers
- **Hierarchical chunking**: 3 levels (document → section → semantic)
- **Zero-overlap chunking**: For 100% literal compliance accuracy

**Fallback Chain**: PyMuPDF → Docling → Regex

### Azure AI Search Schema (29 Fields)

**Core Fields**
- `id`, `content`, `content_vector` (3072-dim), `title`, `reference_number`
- `section`, `citation`, `applies_to`, `date_updated`, `source_file`

**Entity Boolean Filters** (O(1) filtering for multi-tenant queries)
- `applies_to_rumc` (Rush University Medical Center)
- `applies_to_rumg` (Rush University Medical Group)
- `applies_to_rmg` (Rush Medical Group)
- `applies_to_roph` (Rush Oak Park Hospital)
- `applies_to_rcmc` (Rush Copley Medical Center)
- `applies_to_rch` (Rush Children's Hospital)
- `applies_to_roppg` (Rush Oak Park Physicians Group)
- `applies_to_rcmg` (Rush Copley Medical Group)
- `applies_to_ru` (Rush University)

**Hierarchical Chunking**
- `chunk_level`: "document" | "section" | "semantic"
- `parent_chunk_id`, `chunk_index`

### Service Configuration

The chat service uses Azure OpenAI "On Your Data" with vectorSemanticHybrid search plus Cohere Rerank 3.5 for cross-encoder reranking:

| Setting | Value | Description |
|---------|-------|-------------|
| Model | GPT-4.1 | Azure OpenAI chat deployment |
| Query Type | vectorSemanticHybrid | Vector + BM25 + L2 Reranking |
| Top K | 50 | Documents to semantic reranker |
| Index | rush-policies | Azure AI Search index |
| Semantic Config | my-semantic-config | For L2 reranking |
| Cohere Rerank | cohere-rerank-v3-5 | Azure AI Foundry deployment used after retrieval |
| Cohere Top N | 10 | Documents retained post-rerank (configurable) |
| Cohere Min Score | 0.15 | Threshold for healthcare policy precision |

### SDK Dependencies

**Azure Services**
- `openai` - Azure OpenAI client for "On Your Data" (Chat Completions API)
- `azure-search-documents` - Azure AI Search with semantic ranking
- `azure-storage-blob` / `azure.storage.blob.aio` - Async blob storage
- `azure-identity` - DefaultAzureCredential authentication

**Web Framework**
- `fastapi` - Async web framework
- `slowapi` - Rate limiting (30/min default)
- `pydantic-settings` - Type-safe configuration

**PDF Processing**
- `docling` - IBM's PDF processing with TableFormer
- `docling-core` - HierarchicalChunker for section-aware chunking
- `pymupdf` - Raw text extraction for checkbox detection (fixes Docling truncation)

**Embeddings**
- `text-embedding-3-large` (3072 dimensions) via Azure OpenAI

### Healthcare Synonym System

The system uses a two-layer synonym strategy optimized for healthcare terminology:

**Layer 1: Query-Time Expansion** (`app/services/synonym_service.py`)
- Expands user queries BEFORE they reach Azure AI Search
- Affects BOTH vector search (better embeddings) AND keyword search
- Source: `semantic-search-synonyms.json` (1,860 policies analyzed)

```
User: "ED code blue"
  ↓ SynonymService.expand_query()
Expanded: "ED emergency department code blue cardiac arrest"
```

**Layer 2: Index-Time Synonyms** (`azure_policy_index.py`)
- Azure AI Search synonym map for BM25 keyword matching
- 132 rules across 15 categories
- Helps when documents use different terminology than queries

**Synonym Categories:**
| Category | Rules | Examples |
|----------|-------|----------|
| Departments & Units | 18 | ED/ER/emergency department |
| Emergency Codes | 12 | code blue/cardiac arrest |
| Clinical Procedures | 13 | intubation/ETT placement |
| Patient Safety | 10 | fall prevention/fall risk |
| Infection Control | 10 | PPE/personal protective equipment |
| Rush Institution | 9 | RUMC/Rush University Medical Center |
| Compliance | 8 | HIPAA/patient privacy |
| Staff Roles | 8 | RN/registered nurse |
| Equipment | 7 | ventilator/breathing machine |
| Medications | 7 | narcotic/opioid/controlled substance |

**Files:**
- `semantic-search-synonyms.json` - Master synonym database (24 groups)
- `app/services/synonym_service.py` - Query expansion logic
- `azure_policy_index.py:SYNONYMS` - Azure Search synonym map
- `tests/test_synonym_service.py` - Unit tests

### RISEN Prompt Framework
The backend uses a RISEN-based prompt in `policytech_prompt.txt`:
- **R**ole: PolicyTech - strict RAG system, accuracy over satisfaction
- **I**nstructions: Only answer from knowledge base, refuse to fabricate
- **S**teps: Search → Extract → Cite → Format
- **E**nd Goal: Two-part response (Quick Answer + Policy Reference)
- **N**arrowing: RAG-only, refuse non-policy questions, no hallucinations

### RUSH Brand Colors (Tailwind config)
- Primary/Legacy Green: `#006332`
- Secondary/Growth Green: `#30AE6E`
- Tertiary/Vitality Green: `#5FEEA2`
- Background/Sage Green: `#DFF9EB`

### TypeScript Path Alias
`@/*` maps to `./src/*` in the frontend.

## Required Environment Variables

```bash
# Azure AI Search
SEARCH_ENDPOINT=https://policychataisearch.search.windows.net
SEARCH_API_KEY=<key>

# Azure OpenAI
AOAI_ENDPOINT=https://<your-aoai>.openai.azure.com/
AOAI_API_KEY=<api-key>
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
AOAI_CHAT_DEPLOYMENT=gpt-4.1

# Azure Storage (3-container architecture)
STORAGE_CONNECTION_STRING=<connection_string>
SOURCE_CONTAINER_NAME=policies-source     # Staging area
CONTAINER_NAME=policies-active            # Production
# policies-archive is auto-managed        # Deleted policies

# Feature Flags
USE_ON_YOUR_DATA=true                     # Enable vectorSemanticHybrid
# Cohere Rerank 3.5 (cross-encoder)
USE_COHERE_RERANK=true                   # Enable Cohere rerank pipeline
COHERE_RERANK_ENDPOINT=https://<cohere>.models.ai.azure.com
COHERE_RERANK_API_KEY=<api-key>
COHERE_RERANK_MODEL=cohere-rerank-v3-5
COHERE_RERANK_TOP_N=10                   # Docs kept after rerank
COHERE_RERANK_MIN_SCORE=0.15             # Healthcare-calibrated threshold

# Security (production)
REQUIRE_AAD_AUTH=false                    # Set true in production
FAIL_ON_MISSING_CONFIG=false              # Set true in production
CORS_ORIGINS=http://localhost:3000        # Comma-separated for production
ADMIN_API_KEY=<secure-key>                # Required in production

# Backend connection (for frontend)
BACKEND_URL=http://localhost:8000
```

## Azure Blob Storage Architecture

```
Azure Blob Storage (policytechrush)
├── policies-source/     ← DROP NEW/UPDATED PDFs HERE (staging)
├── policies-active/     ← PRODUCTION (auto-synced from source)
└── policies-archive/    ← DELETED POLICIES (moved here, not deleted)
```

**Key Principle**: Only upload to `policies-source`. The sync system handles everything else automatically using SHA-256 content hashing to detect changes.

## Monthly Policy Update Procedures

The system supports versioned policy updates with full audit trail. See [docs/MONTHLY_UPDATE_PROCEDURES.md](docs/MONTHLY_UPDATE_PROCEDURES.md) for detailed procedures.

### Quick Reference

**New Policies (v1.0)**
```bash
# 1. Upload new PDF to staging
az storage blob upload --container policies-source --file NewPolicy.pdf

# 2. Run sync (creates v1.0 chunks)
cd apps/backend && python policy_sync.py sync
```

**Updated Policies (v1 → v2 transitions)**
```bash
# 1. Upload updated PDF to staging (same filename)
az storage blob upload --container policies-source --file ExistingPolicy.pdf --overwrite

# 2. Run sync (old chunks marked SUPERSEDED, new chunks created as v2.0)
cd apps/backend && python policy_sync.py sync
```

**Retiring Policies**
```bash
# Remove from staging - sync will archive and mark chunks as RETIRED
az storage blob delete --container policies-source --name RetiredPolicy.pdf
cd apps/backend && python policy_sync.py sync
```

### Version Control Fields (36-field schema)
| Field | Type | Purpose |
|-------|------|---------|
| `version_number` | String | Version (e.g., "1.0", "2.0") |
| `version_sequence` | Int32 | Numeric for sorting |
| `policy_status` | String | ACTIVE, SUPERSEDED, RETIRED, DRAFT |
| `effective_date` | DateTime | When version took effect |
| `expiration_date` | DateTime | When superseded/retired |
| `superseded_by` | String | Version that replaced this |

### Status Lifecycle
```
DRAFT → ACTIVE → SUPERSEDED → RETIRED
              ↓
        (always searchable with policy_status filter)
```

## Design Guidelines

See `/apps/frontend/design_guidelines.md` for complete RUSH brand design system including:
- Typography (Calibre Semibold headings, Georgia for emphasis)
- Accessibility requirements (WCAG 2.1 AA)
- Component specifications

## API Documentation

When backend is running: http://localhost:8000/docs (Swagger) or http://localhost:8000/redoc
