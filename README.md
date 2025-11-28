# RUSH Policy RAG Agent

Production-ready RAG (Retrieval-Augmented Generation) system for RUSH University System for Health policy retrieval.

**Tech Stack**: FastAPI backend + Next.js 14 frontend + Azure OpenAI "On Your Data" (vectorSemanticHybrid)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PRODUCTION ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Browser                                                                │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Next.js 14 Frontend (Port 3000)                                    │    │
│  │  ├── App Router (/src/app/)                                         │    │
│  │  ├── API Proxy Routes → FastAPI                                     │    │
│  │  ├── Security Headers (CSP, HSTS, X-Frame-Options)                  │    │
│  │  └── RUSH Brand Styling                                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼ HTTP/JSON                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  FastAPI Backend (Port 8000)                                        │    │
│  │  ├── /api/chat - Policy Q&A via Azure OpenAI "On Your Data"        │    │
│  │  ├── /api/search - Direct Azure AI Search                          │    │
│  │  ├── /api/pdf/{filename} - PDF viewing (SAS URLs)                  │    │
│  │  ├── /api/upload - PDF upload & indexing                           │    │
│  │  ├── /api/admin/* - Admin endpoints                                │    │
│  │  ├── /health - Health check                                        │    │
│  │  ├── Rate Limiting (slowapi - 30/min)                              │    │
│  │  ├── Async I/O (azure.storage.blob.aio)                            │    │
│  │  └── Azure AD Authentication (optional)                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Azure Services                                                      │    │
│  │  ├── Azure OpenAI "On Your Data"                                    │    │
│  │  │   ├── GPT-4.1 Chat Completions                                   │    │
│  │  │   ├── vectorSemanticHybrid Search (Vector + BM25 + L2 Reranking)│    │
│  │  │   └── semantic_configuration for best quality                    │    │
│  │  ├── Azure AI Search (rush-policies index)                          │    │
│  │  │   ├── 29-field schema                                           │    │
│  │  │   ├── 132 synonym rules                                         │    │
│  │  │   └── text-embedding-3-large (3072-dim)                         │    │
│  │  └── Azure Blob Storage (policies-active)                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  NO AZURE FUNCTIONS - Pure FastAPI + Next.js                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Features

- **Azure OpenAI "On Your Data"**: vectorSemanticHybrid search (Vector + BM25 + L2 Reranking)
- **Production Security**: Rate limiting, input validation, CSP, HSTS, Azure AD auth support
- **PDF Upload & Viewing**: End-to-end pipeline with async blob storage
- **1,800+ Document Support**: top_k=50 with semantic ranker optimization
- **Zero-Overlap Chunking**: For literal compliance accuracy

## Architecture Status: ✅ COMPLETED

The stack has been simplified to **FastAPI + Next.js only** (no Azure Functions). See [`docs/SINGLE_BACKEND_SIMPLIFICATION.md`](docs/SINGLE_BACKEND_SIMPLIFICATION.md) for details.

## Azure Services Required

> **Important**: This application **requires** Azure services to function. It cannot run without Azure AI Search (vector store) and Azure Blob Storage (PDF storage).

| Azure Service | Purpose | Required |
|---------------|---------|----------|
| **Azure AI Search** | Vector store (3072-dim embeddings) + semantic ranking + 132 synonym rules | ✅ **Required** |
| **Azure OpenAI** | GPT-4.1 (chat) + text-embedding-3-large (embeddings) | ✅ **Required** |
| **Azure Blob Storage** | PDF document storage (3 containers: source, active, archive) | ✅ **Required** |
| **Azure Container Apps** | Host FastAPI backend + Next.js frontend | ✅ **Required** |
| **Azure AD** | Authentication for production | Optional |

**Why these are required:**
- **Azure AI Search**: Stores document embeddings (3072-dim vectors), enables hybrid search, and provides L2 semantic reranking
- **Azure Blob Storage**: Stores the actual PDF files; required for the PDF viewing feature and document sync
- **Azure OpenAI**: Powers the chat (GPT-4.1) and generates embeddings (text-embedding-3-large)

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step Azure resource creation commands.

## Quick Start

### 1. Prerequisites

- Python 3.9+
- Node.js 18+
- Azure AI Search service with `rush-policies` index
- Azure OpenAI service (GPT-4.1 + text-embedding-3-large)
- Azure Blob Storage account with 3 containers

### 2. Environment Setup

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
```

### 3. Start Services

```bash
# Terminal 1: Backend
./start_backend.sh

# Terminal 2: Frontend
./start_frontend.sh
```

Open http://localhost:3000 and ask a policy question.

## Project Structure

```
rag_pt_rush/
├── apps/
│   ├── backend/                       # FastAPI backend
│   │   ├── main.py                    # API entrypoint
│   │   ├── app/
│   │   │   ├── services/
│   │   │   │   ├── on_your_data_service.py  # Azure OpenAI "On Your Data"
│   │   │   │   ├── chat_service.py          # Chat orchestration
│   │   │   │   ├── upload_service.py        # PDF upload & indexing
│   │   │   │   └── synonym_service.py       # Query-time synonym expansion
│   │   │   ├── api/routes/            # API endpoints
│   │   │   └── core/
│   │   │       ├── config.py          # Pydantic settings
│   │   │       ├── rate_limit.py      # slowapi rate limiting
│   │   │       └── auth.py            # Azure AD authentication
│   │   ├── azure_policy_index.py      # Search index + 132 synonym rules
│   │   ├── preprocessing/
│   │   │   └── chunker.py             # PDF chunking (Docling + PyMuPDF)
│   │   └── policytech_prompt.txt      # RISEN prompt
│   └── frontend/                      # Next.js 14 app
│       ├── src/app/                   # App Router
│       ├── src/components/            # UI components
│       └── next.config.js             # Security headers
├── scripts/
│   └── upload_pdfs_to_blob.py         # PDF upload utility
├── docs/
│   ├── SINGLE_BACKEND_SIMPLIFICATION.md  # Architecture decision
│   └── CHANGELOG.md                   # Release notes
├── semantic-search-synonyms.json      # 24 synonym groups
├── start_backend.sh                   # Backend launcher
├── start_frontend.sh                  # Frontend launcher
└── .env                               # Environment variables
```

### Query Flow

```
User Query: "ED code blue policy"
    ↓
Synonym Expansion (SynonymService)
├── "ED" → "ED emergency department"
├── "code blue" → "code blue cardiac arrest"
└── Misspelling correction (if needed)
    ↓
Expanded: "ED emergency department code blue cardiac arrest policy"
    ↓
Azure OpenAI "On Your Data" (vectorSemanticHybrid)
├── Vector Search (text-embedding-3-large)
├── BM25 + 132 Synonym Rules (keyword matches)
├── L2 Semantic Reranking (top 50 → best 5)
└── semantic_configuration for quality
    ↓
GPT-4.1 + RISEN Prompt
    ↓
Styled Response with Citations
```

## Document Ingestion & Sync Workflow

### Azure Blob Storage Container Structure

```
Azure Blob Storage (policytechrush)
├── policies-source/     ← DROP NEW/UPDATED PDFs HERE (staging area)
├── policies-active/     ← PRODUCTION (auto-synced from source)
└── policies-archive/    ← DELETED POLICIES (moved here, not deleted)
```

**Key Principle**: You only ever upload to `policies-source`. The sync system handles everything else automatically and only re-processes documents that actually changed (based on SHA-256 content hash).

---

### Initial Ingestion (One-Time Setup)

**Step 1: Setup Azure Infrastructure**

```bash
cd apps/backend
source ../../.venv/bin/activate
python scripts/setup_azure_infrastructure.py
```

This creates:
- Blob containers (`policies-source`, `policies-active`, `policies-archive`)
- Azure AI Search index (`rush-policies`) with 29 fields
- Synonym map for medical terms
- Validates Azure OpenAI embeddings

**Step 2: Upload PDFs to policies-source**

Using Azure Storage Explorer, Azure Portal, or azcopy:
```bash
# Using azcopy
azcopy copy "./local-policies/*.pdf" \
  "https://policytechrush.blob.core.windows.net/policies-source?<SAS_TOKEN>"

# Or using the blob_ingest script
python blob_ingest.py --source /path/to/local/policies --container policies-source
```

**Step 3: Run Full Ingestion**

```bash
python scripts/ingest_all_policies.py --source-container policies-source
```

This will:
1. Read each PDF from `policies-source`
2. Parse with Docling (TableFormer for tables, checkbox detection)
3. Extract 9 entity boolean filters (RUMC, RUMG, RMG, etc.)
4. Chunk hierarchically (section → semantic, ~1,500 chars)
5. Generate 3072-dim embeddings (text-embedding-3-large)
6. Upload chunks to Azure AI Search index
7. Copy processed PDFs to `policies-active` with content hash metadata

**Validation (Optional)**
```bash
# Parse PDFs without uploading to verify extraction quality
python scripts/ingest_all_policies.py --validate-only --sample 20
```

---

### Monthly Maintenance (Incremental Sync)

The sync system uses **content hashes** stored in blob metadata to detect changes. Only changed/new documents get re-chunked - no full reindex needed.

**Step 1: Upload Changed PDFs to policies-source**

| Action | What to Do |
|--------|------------|
| **New policy** | Upload PDF to `policies-source` |
| **Updated policy** | Upload new version with same filename (overwrites) |
| **Deleted policy** | Don't upload (sync detects missing files) |

**Step 2: Preview Changes (Recommended)**

```bash
python policy_sync.py detect policies-source policies-active
```

Output shows:
```
New: 3
  + NewHRPolicy.pdf
  + CyberSecurityGuidelines.pdf
  + ...

Changed: 12
  ~ InfectionControl.pdf (hash mismatch)
  ~ MedicationAdministration.pdf
  ~ ...

Deleted: 2
  - ObsoletePolicy2019.pdf
  - ...
```

**Step 3: Run Sync**

```bash
python policy_sync.py sync policies-source policies-active
```

This will:
1. **Compare** `policies-source` vs `policies-active` using content hashes
2. **For new/changed docs only**:
   - Delete old chunks from search index (if updating)
   - Re-chunk the PDF with Docling
   - Generate new embeddings
   - Upload to search index
   - Copy to `policies-active` with new hash
3. **For deleted docs**:
   - Move to `policies-archive` (not permanently deleted)
   - Delete chunks from search index

**Dry Run Mode**
```bash
python policy_sync.py sync --dry-run  # See what would change without applying
```

---

### Visual Workflow

```
INITIAL INGESTION:
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Local PDFs     │────▶│ policies-source │────▶│ policies-active │
│  (1,800 files)  │     │  (staging)      │     │  (production)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                │
                                ▼
                        ┌─────────────────┐
                        │ Azure AI Search │
                        │ rush-policies   │
                        │ (~10k chunks)   │
                        └─────────────────┘

MONTHLY SYNC:
┌─────────────────┐     ┌─────────────────┐
│ Updated PDFs    │────▶│ policies-source │
│ (50 changed)    │     │                 │
└─────────────────┘     └────────┬────────┘
                                 │
                                 ▼  Detect (hash compare)
                        ┌─────────────────┐
                        │ 3 new           │
                        │ 12 changed      │
                        │ 2 deleted       │
                        └────────┬────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        ┌──────────┐      ┌──────────┐      ┌──────────┐
        │ Chunk 3  │      │ Re-chunk │      │ Archive 2│
        │ new PDFs │      │ 12 PDFs  │      │ policies │
        └────┬─────┘      └────┬─────┘      └────┬─────┘
             │                 │                 │
             └─────────────────┼─────────────────┘
                               ▼
                       ┌─────────────────┐
                       │ Azure AI Search │
                       │ (incremental)   │
                       └─────────────────┘
```

---

### Emergency Full Reindex

If you need to rebuild the entire index (e.g., schema change):

```bash
# 1. Clear storage and index
python scripts/setup_azure_infrastructure.py  # Recreates index with fresh schema

# 2. Full reindex from policies-active
python policy_sync.py reindex policies-active
```

---

### CLI Command Reference

| Task | Command |
|------|---------|
| Setup infrastructure | `python scripts/setup_azure_infrastructure.py` |
| Initial ingestion | `python scripts/ingest_all_policies.py --source-container policies-source` |
| Validate extraction | `python scripts/ingest_all_policies.py --validate-only --sample 20` |
| Detect changes | `python policy_sync.py detect` |
| Run monthly sync | `python policy_sync.py sync` |
| Dry run sync | `python policy_sync.py sync --dry-run` |
| Full reindex | `python policy_sync.py reindex policies-active` |
| Process single PDF | `python policy_sync.py process /path/to/file.pdf` |
| Test search | `python azure_policy_index.py search "chaperone policy"` |
| Index stats | `python azure_policy_index.py stats` |
| Update synonyms | `python azure_policy_index.py synonyms` |
| Test synonym expansion | `python azure_policy_index.py test-synonyms "ED code blue"` |
| Run synonym tests | `python -m pytest tests/test_synonym_service.py -v` |

---

### Index Schema (29 Fields)

The search index includes:

**Core Fields**
- `id`, `content`, `content_vector` (3072-dim), `title`, `reference_number`
- `section`, `citation`, `applies_to`, `date_updated`, `source_file`

**Entity Boolean Filters** (for O(1) filtering)
- `applies_to_rumc`, `applies_to_rumg`, `applies_to_rmg`, `applies_to_roph`
- `applies_to_rcmc`, `applies_to_rch`, `applies_to_roppg`, `applies_to_rcmg`, `applies_to_ru`

**Hierarchical Chunking**
- `chunk_level` ("document" | "section" | "semantic")
- `parent_chunk_id`, `chunk_index`

**Enhanced Metadata**
- `category`, `subcategory`, `regulatory_citations`, `related_policies`

Example entity-filtered search:
```bash
python azure_policy_index.py search "chaperone policy" --filter "applies_to_rumc eq true"
```

---

## PDF Processing & Checkbox Extraction

### PyMuPDF Checkbox Extraction

The "Applies To" checkboxes (RUMC, RUMG, RMG, ROPH, RCMC, RCH) are critical for entity filtering but proved challenging to extract reliably:

**Problem**: Docling's TableFormer sometimes truncates checkbox rows in the PDF header tables, resulting in missing or incomplete entity lists.

**Solution**: PyMuPDF-first extraction strategy:

```
PDF Processing Pipeline:
1. PyMuPDF (fitz) - Extract raw text from first page
   └── Regex for "Applies To" section with checkbox patterns
2. Docling fallback - If PyMuPDF finds no checkboxes
   └── TableFormer ACCURATE mode with cell matching
3. Regex fallback - Last resort pattern matching
```

**Implementation** (in `apps/backend/preprocessing/chunker.py`):
- `_extract_applies_to_from_raw_pdf()` - PyMuPDF raw text extraction (lines 687-750)
- Checkbox patterns: `☒`, `☐`, `✓`, `✔`, `■`
- Entity extraction: `{entity} ☒` or `☒ {entity}` patterns

**Results**: NPO Policy now correctly extracts `Applies To: RUMC, RUMG, ROPH, RCH` (was truncating to just `RUMC`).

---

## Full Pipeline Ingestion

### `full_pipeline_ingest.py`

Complete document ingestion pipeline with parallel processing:

```bash
python scripts/full_pipeline_ingest.py
```

**Pipeline Steps**:
1. **Download** - Fetch PDFs from Azure Blob Storage (policies-active)
2. **Chunk** - Docling + PyMuPDF processing (~1,500 char chunks)
3. **Embed** - Azure OpenAI text-embedding-3-large (3072-dim)
4. **Upload** - Azure AI Search index with all 29 fields

**Performance Benchmarks**:
| Documents | Chunks | Time | Rate |
|-----------|--------|------|------|
| 50 | 989 | ~85-113s | 0.6-0.9 docs/sec |

**Parallel Processing**:
- `ThreadPoolExecutor` for concurrent blob downloads
- Batch embedding generation (100 chunks/batch)
- Chunked index uploads (500 docs/batch)

---

## Test Strategy

### Evaluation Framework

The RAG system is tested against a curated dataset of policy questions:

```bash
# Run full test suite
python scripts/run_test_dataset.py

# View results
cat test_results.json | jq '.summary'
```

**Test Categories**:
| Category | Description | Count |
|----------|-------------|-------|
| `general` | Standard policy questions | 12 |
| `edge_case` | Boundary conditions | 5 |
| `adversarial` | Injection/manipulation attempts | 3 |
| `multi_policy` | Cross-policy questions | 4 |
| `not_found` | Questions with no policy answer | 3 |

**Current Pass Rate**: 88.9% (24/27 tests passing)

**Test Dataset Location**: `apps/backend/tests/test_dataset.json`

**Evaluation Criteria**:
- **Accuracy**: Does response match expected content?
- **Citations**: Are policy references included?
- **Refusal**: Does system refuse non-policy questions?

## API Documentation

When backend is running:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Chat Endpoint

**POST** `/api/chat`

```json
{
  "message": "Who can accept verbal orders?",
  "filter_applies_to": ["RUMC", "RMG"]
}
```

## Troubleshooting

### Backend Issues
- **Connection errors**: Check `AOAI_ENDPOINT` and `SEARCH_ENDPOINT` in `.env`
- **Rate limit errors**: Default is 30/min - check `app/core/rate_limit.py`
- **Auth errors**: Set `REQUIRE_AAD_AUTH=false` for local development

### Search Issues
- **No results**: Verify index has documents (`python azure_policy_index.py stats`)
- **Low relevance**: Ensure `USE_ON_YOUR_DATA=true` for vectorSemanticHybrid

### PDF Issues
- **Upload fails**: Check `STORAGE_CONNECTION_STRING` is valid
- **404 on PDF view**: Verify PDF exists in `policies-active` container

## License

Internal use only - Rush University System for Health

## Support

- Check CLAUDE.md for development guidance
- See [DEPLOYMENT.md](DEPLOYMENT.md) for Azure deployment steps
- Contact Policy Administration at https://rushumc.navexone.com/
