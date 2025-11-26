# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RUSH Policy RAG Agent - A full-stack RAG (Retrieval-Augmented Generation) system for policy retrieval at Rush University System for Health.

**Tech Stack**: Next.js 14 frontend + FastAPI backend + Azure AI Foundry Agents + Docling PDF processing + PyMuPDF checkbox extraction

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
│  User Browser                                                               │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────┐                                                        │
│  │ Next.js Frontend│ (:3000)                                                │
│  │ RUSH brand styling, metadata display                                     │
│  └────────┬────────┘                                                        │
│           │ POST /api/chat                                                   │
│           ▼                                                                  │
│  ┌─────────────────┐                                                        │
│  │ FastAPI Backend │ (:8000)                                                │
│  └────────┬────────┘                                                        │
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
│  │  Azure AI Foundry Agent (Persistent)                    │                │
│  │  ├── Agent ID: asst_WQSFmyXMHpJedZM0Rwo43zk1           │                │
│  │  ├── Model: GPT-4.1                                     │                │
│  │  └── Tool: Azure AI Search (VECTOR_SEMANTIC_HYBRID)     │                │
│  │           ├── Vector search (embedding similarity)      │                │
│  │           ├── BM25 + 132 synonym rules (keyword match)  │                │
│  │           ├── RRF fusion (combine results)              │                │
│  │           └── L2 reranking (top_k=50 → best results)    │                │
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

### Agent Management (from root)
```bash
# Create/update the persistent Foundry Agent
python scripts/create_foundry_agent.py              # Create new agent
python scripts/create_foundry_agent.py --update     # Update existing
python scripts/create_foundry_agent.py --list       # List all agents
python scripts/create_foundry_agent.py --delete     # Delete agent
```

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
# Run full RAG test suite (27 test cases, 88.9% pass rate)
python scripts/run_test_dataset.py

# View test results summary
cat test_results.json | jq '.summary'

# Debug PDF checkbox extraction
python scripts/debug_pdf_structure.py

# A/B test checkbox extraction methods
python scripts/test_checkbox_extraction.py
```

## Project Structure

```
rag_pt_rush/
├── apps/
│   ├── backend/                         # FastAPI backend
│   │   ├── main.py                      # API entrypoint
│   │   ├── agent_config.json            # Persistent agent config
│   │   ├── app/
│   │   │   ├── services/
│   │   │   │   ├── foundry_agent.py     # AgentsClient SDK wrapper
│   │   │   │   ├── chat_service.py      # Chat orchestration
│   │   │   │   └── synonym_service.py   # Query-time synonym expansion
│   │   │   └── api/routes/              # API endpoints
│   │   ├── preprocessing/
│   │   │   ├── chunker.py               # Docling + PyMuPDF PDF chunker
│   │   │   └── archive/                 # Legacy code (deprecated)
│   │   ├── scripts/
│   │   │   ├── ingest_all_policies.py   # Full ingestion pipeline
│   │   │   └── setup_azure_infrastructure.py
│   │   ├── azure_policy_index.py        # Azure AI Search + 132 synonym rules
│   │   ├── policy_sync.py               # Differential sync (content hashing)
│   │   └── policytech_prompt.txt        # RISEN prompt framework
│   └── frontend/                        # Next.js 14 app
│       ├── src/app/                     # App Router
│       └── src/components/              # UI components (RUSH branding)
├── scripts/
│   ├── create_foundry_agent.py          # Agent management CLI
│   └── upload_pdfs_to_blob.py           # PDF upload to Azure Blob Storage
├── semantic-search-synonyms.json        # 24 synonym groups, 155 abbrevs, 56 misspellings
├── start_backend.sh                     # Backend launcher
├── start_frontend.sh                    # Frontend launcher
└── .env                                 # Environment variables
```

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

### Agent Configuration
```json
{
  "agent_id": "asst_WQSFmyXMHpJedZM0Rwo43zk1",
  "agent_name": "rush-policy-agent",
  "model": "gpt-4.1",
  "query_type": "VECTOR_SEMANTIC_HYBRID",
  "top_k": 50,
  "index_name": "rush-policies"
}
```

### SDK Dependencies

**Azure AI Foundry**
- `azure-ai-agents` - AgentsClient for persistent agent operations
- `azure-ai-projects` - AIProjectClient for connections management
- `azure-search-documents` - Azure AI Search with semantic ranking
- `azure-identity` - DefaultAzureCredential authentication

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
# Azure AI Foundry
AZURE_AI_PROJECT_ENDPOINT=https://<ai-services>.services.ai.azure.com/api/projects/<project>

# Persistent Agent (created via scripts/create_foundry_agent.py)
FOUNDRY_AGENT_ID=asst_WQSFmyXMHpJedZM0Rwo43zk1

# Azure AI Search
SEARCH_ENDPOINT=https://policychataisearch.search.windows.net
SEARCH_API_KEY=<key>

# Azure Storage (3-container architecture)
STORAGE_CONNECTION_STRING=<connection_string>
SOURCE_CONTAINER_NAME=policies-source     # Staging area
CONTAINER_NAME=policies-active            # Production
# policies-archive is auto-managed        # Deleted policies

# Azure OpenAI (embeddings)
AOAI_ENDPOINT=https://<your-aoai>.openai.azure.com/
AOAI_API=<api-key>
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
AOAI_CHAT_DEPLOYMENT=gpt-4.1

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

## Design Guidelines

See `/apps/frontend/design_guidelines.md` for complete RUSH brand design system including:
- Typography (Calibre Semibold headings, Georgia for emphasis)
- Accessibility requirements (WCAG 2.1 AA)
- Component specifications

## API Documentation

When backend is running: http://localhost:8000/docs (Swagger) or http://localhost:8000/redoc
