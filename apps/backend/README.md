# RUSH Policy RAG Backend

FastAPI backend for the RUSH Policy RAG system. Integrates with Azure OpenAI "On Your Data" (vectorSemanticHybrid search) and Cohere Rerank 3.5 for intelligent policy retrieval.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Azure Blob Storage               Docling Processing            Azure AI Search
│  ┌─────────────────┐             ┌──────────────────┐          ┌─────────────┐
│  │ policies-source │ ──────────► │  PolicyChunker   │ ───────► │rush-policies│
│  │  (staging)      │   Download  │  - TableFormer   │  Upload  │   index     │
│  └─────────────────┘             │  - Checkboxes    │          │ (36 fields) │
│                                  │  - 9 entity flags │          │ (3072-dim)  │
│  ┌─────────────────┐             └──────────────────┘          └──────┬──────┘
│  │ policies-active │                                                  │
│  │  (production)   │ ◄───────── Copy after successful ingestion       │
│  └─────────────────┘                                                  │
│                                                                       │
│  ┌─────────────────┐                                                  │
│  │ policies-archive│ ◄───────── Deleted policies (soft delete)       │
│  └─────────────────┘                                                  │
│                                                                       │
├───────────────────────────────────────────────────────────────────────┤
│                              QUERY PIPELINE                           │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│   Frontend (:3000)     FastAPI (:8000)                               │
│  ┌─────────────┐      ┌─────────────────────────────────────────┐    │
│  │ POST /chat  │ ───► │ ChatService                              │    │
│  │             │      │   ↓                                      │    │
│  │             │      │ Query Validation + Disambiguation        │    │
│  │             │      │   ↓                                      │    │
│  │             │      │ Synonym Expansion (155+ medical terms)   │    │
│  │             │      │   ↓                                      │    │
│  │ Response    │ ◄─── │ OnYourDataService (vectorSemanticHybrid) │    │
│  │ + Citations │      │   ↓                                      │    │
│  └─────────────┘      │ Cohere Rerank 3.5 (cross-encoder)        │    │
│                       │   ↓                                      │    │
│                       │ Response Formatting + Citations          │    │
│                       └─────────────────────────────────────────┘    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Setup

### 1. Install Dependencies

```bash
cd apps/backend
pip install -r requirements.txt

# Docling is required for PDF processing
pip install docling docling-core
```

### 2. Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Azure AI Search (REQUIRED)
SEARCH_ENDPOINT=https://policychataisearch.search.windows.net
SEARCH_API_KEY=your_key

# Azure OpenAI (REQUIRED)
AOAI_ENDPOINT=https://<your-aoai>.openai.azure.com/
AOAI_API_KEY=your_api_key
AOAI_CHAT_DEPLOYMENT=gpt-4.1
AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Azure Storage (REQUIRED for PDF viewing)
STORAGE_CONNECTION_STRING=your_connection_string
SOURCE_CONTAINER_NAME=policies-source
CONTAINER_NAME=policies-active

# Feature Flags
USE_ON_YOUR_DATA=true
USE_COHERE_RERANK=true

# Cohere Rerank 3.5 (REQUIRED for healthcare RAG quality)
COHERE_RERANK_ENDPOINT=https://<cohere>.models.ai.azure.com
COHERE_RERANK_API_KEY=your_key
COHERE_RERANK_MODEL=cohere-rerank-v3-5
COHERE_RERANK_TOP_N=10
COHERE_RERANK_MIN_SCORE=0.25
```

See `.env.example` for the complete list of configuration options.

### 3. Run the Server

```bash
cd apps/backend
python main.py
```

Or with uvicorn directly:
```bash
uvicorn main:app --reload --port 8000
```

The server starts on `http://localhost:8000`

## Document Ingestion Pipeline

### Overview

The ingestion pipeline uses IBM Docling for PDF processing:

1. **PDF Upload** → Upload PDFs to `policies-source` blob container
2. **Docling Processing** → TableFormer table extraction + checkbox detection
3. **Chunking** → Hierarchical section-aware chunking (~1,500 chars)
4. **Embedding** → Generate 3072-dim vectors (text-embedding-3-large)
5. **Indexing** → Upload to Azure AI Search with 36-field schema
6. **Sync** → Copy to `policies-active` with content hash metadata

### Key Features

- **TableFormer ACCURATE**: Superior table extraction from policy header tables
- **Checkbox Detection**: Native extraction of "Applies To" entity checkboxes
- **9 Entity Boolean Filters**: RUMC, RUMG, RMG, ROPH, RCMC, RCH, ROPPG, RCMG, RU
- **Content Hashing**: SHA-256 for differential sync (only re-process changed docs)
- **Hierarchical Chunking**: 3 levels (document → section → semantic)
- **Version Control**: Track policy versions with status lifecycle (ACTIVE, SUPERSEDED, RETIRED)

### CLI Commands

```bash
cd apps/backend

# Full ingestion from Azure Blob Storage
python scripts/ingest_all_policies.py

# Validate PDF parsing without uploading (test extraction quality)
python scripts/ingest_all_policies.py --validate-only --sample 20

# Process a local folder of PDFs
python scripts/ingest_all_policies.py --local-folder ./data/policies

# Save detailed report to JSON
python scripts/ingest_all_policies.py --output-report /tmp/ingest_report.json

# Detect changes (preview what would be synced)
python policy_sync.py detect

# Run incremental sync (only changed documents)
python policy_sync.py sync

# Dry run sync (see changes without applying)
python policy_sync.py sync --dry-run

# Force full reindex
python scripts/ingest_all_policies.py --force-reindex
```

## Azure AI Search Schema

The `rush-policies` index uses a 36-field schema:

### Core Fields
| Field | Type | Purpose |
|-------|------|---------|
| `id` | String (key) | Unique chunk identifier |
| `content` | String | Full chunk text (searchable) |
| `content_vector` | Vector (3072-dim) | Embeddings for semantic search |
| `title` | String | Policy title |
| `reference_number` | String | Policy reference number |
| `section` | String | Section number and title |
| `citation` | String | Full citation for attribution |
| `applies_to` | String | Comma-separated entity list |
| `date_updated` | String | Last update date |
| `source_file` | String | Original PDF filename |
| `content_hash` | String | SHA-256 hash for change detection |
| `document_owner` | String | Policy owner name |

### Entity Boolean Filters
| Field | Entity |
|-------|--------|
| `applies_to_rumc` | Rush University Medical Center |
| `applies_to_rumg` | Rush University Medical Group |
| `applies_to_rmg` | Rush Medical Group |
| `applies_to_roph` | Rush Oak Park Hospital |
| `applies_to_rcmc` | Rush Copley Medical Center |
| `applies_to_rch` | Rush Children's Hospital |
| `applies_to_roppg` | Rush Oak Park Physicians Group |
| `applies_to_rcmg` | Rush Copley Medical Group |
| `applies_to_ru` | Rush University |

### Version Control Fields
| Field | Type | Purpose |
|-------|------|---------|
| `version_number` | String | Version (e.g., "1.0", "2.0") |
| `version_sequence` | Int32 | Numeric for sorting |
| `policy_status` | String | ACTIVE, SUPERSEDED, RETIRED, DRAFT |
| `effective_date` | DateTime | When version took effect |
| `expiration_date` | DateTime | When superseded/retired |
| `superseded_by` | String | Version that replaced this |

### Hierarchical Chunking Fields
| Field | Values | Purpose |
|-------|--------|---------|
| `chunk_level` | "document", "section", "semantic" | Granularity level |
| `parent_chunk_id` | String | Parent for nested chunks |
| `chunk_index` | Int | Order within document |

## API Endpoints

### `GET /health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "search_index": {
    "index_name": "rush-policies",
    "document_count": 16980,
    "fields": 36
  },
  "on_your_data": {
    "configured": true,
    "query_type": "vectorSemanticHybrid"
  },
  "version": "3.0.0"
}
```

### `POST /api/chat`
Process a chat message and return RAG response.

**Request:**
```json
{
  "message": "Who can accept verbal orders?",
  "filter_applies_to": ["RUMC", "RMG"]
}
```

**Response:**
```json
{
  "response": "Based on RUSH policies...",
  "confidence": "high",
  "sources": [
    {
      "title": "Verbal and Telephone Orders",
      "reference_number": "486",
      "section": "II. Policy",
      "applies_to": "RUMC",
      "date_updated": "02/02/2024"
    }
  ],
  "evidence": [
    {
      "text": "Relevant policy excerpt...",
      "source": "Policy 486",
      "score": 0.95
    }
  ]
}
```

### `POST /api/chat/stream`

Streaming chat endpoint using Server-Sent Events (SSE).

### `POST /api/search-instances`

Search for specific text within a single policy document.

### `GET /api/pdf/{filename}`

Get a signed URL for viewing a policy PDF.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI application entrypoint |
| `app/services/chat_service.py` | Main RAG orchestrator (2,200+ lines) |
| `app/services/on_your_data_service.py` | Azure OpenAI "On Your Data" integration |
| `app/services/cohere_rerank_service.py` | Cohere Rerank 3.5 cross-encoder |
| `app/services/synonym_service.py` | Healthcare synonym expansion |
| `app/services/device_disambiguator.py` | IV/catheter/line disambiguation |
| `preprocessing/chunker.py` | Docling-based PDF chunker |
| `scripts/ingest_all_policies.py` | Full ingestion pipeline |
| `azure_policy_index.py` | Azure AI Search index management |
| `policy_sync.py` | Differential sync with content hashing |
| `policytech_prompt.txt` | RISEN prompt framework |

## CORS Configuration

The backend allows requests from:
- `http://localhost:3000` (Next.js default)
- `http://localhost:5000` (Alternative port)
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5000`

Configure via `CORS_ORIGINS` environment variable for production.

## Troubleshooting

### Startup Issues

- **Missing AOAI_API_KEY**: Ensure `AOAI_API_KEY` is set (not `AOAI_API`)
- **Search index not found**: Verify `SEARCH_ENDPOINT` and `SEARCH_API_KEY`
- **Circular import errors**: Check `app/services/__init__.py` for lazy imports

### Ingestion Issues

- **Docling not found**: Install with `pip install docling docling-core`
- **Empty chunks**: Check PDF is not scanned (OCR not enabled)
- **Missing metadata**: Check header table format matches expected patterns

### Search Issues

- **No results**: Verify index has documents (`/health` endpoint shows count)
- **Low relevance**: Check Cohere Rerank is configured (`USE_COHERE_RERANK=true`)
- **Disambiguation not triggering**: Ensure query validation runs before cache check

### Cohere Rerank Issues

- **401 Unauthorized**: Verify `COHERE_RERANK_API_KEY` is correct
- **Endpoint not found**: Ensure `COHERE_RERANK_ENDPOINT` includes full URL

## API Documentation

When the server is running:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
