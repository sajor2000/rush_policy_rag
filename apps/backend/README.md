# RUSH Policy RAG Backend

FastAPI backend for the RUSH Policy RAG system. Integrates with Azure AI Foundry Agents and Azure AI Search for intelligent policy retrieval.

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
│  └─────────────────┘             │  - Checkboxes    │          │ (29 fields) │
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
│   Frontend (:3000)     FastAPI (:8000)      Azure AI Foundry Agent   │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────────────┐   │
│  │ POST /chat  │ ───► │ /api/chat   │ ───► │ Persistent Agent    │   │
│  │             │      │             │      │ (GPT-4.1)           │   │
│  │ Response    │ ◄─── │ Response    │ ◄─── │ ↓                   │   │
│  │ + Citations │      │ + Metadata  │      │ VECTOR_SEMANTIC_    │───┘
│  └─────────────┘      └─────────────┘      │ HYBRID Search       │
│                                            │ (top_k=50, L2 rank) │
│                                            └─────────────────────┘
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

```bash
# Azure AI Foundry (required for agents)
export AZURE_AI_PROJECT_ENDPOINT="https://<ai-services>.services.ai.azure.com/api/projects/<project>"
export FOUNDRY_AGENT_ID="asst_gtXECcCrc3sKu9tlBfVtqSr3"

# Azure AI Search (required for vector store)
export SEARCH_ENDPOINT="https://policychataisearch.search.windows.net"
export SEARCH_API_KEY="your_key"

# Azure Storage (required for PDF ingestion)
export STORAGE_CONNECTION_STRING="your_connection_string"
export SOURCE_CONTAINER_NAME="policies-source"
export CONTAINER_NAME="policies-active"

# Azure OpenAI (embeddings)
export AOAI_ENDPOINT="https://<your-aoai>.openai.azure.com/"
export AOAI_API="your_api_key"
export AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large"
```

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
5. **Indexing** → Upload to Azure AI Search with 29-field schema
6. **Sync** → Copy to `policies-active` with content hash metadata

### Key Features

- **TableFormer ACCURATE**: Superior table extraction from policy header tables
- **Checkbox Detection**: Native extraction of "Applies To" entity checkboxes
- **9 Entity Boolean Filters**: RUMC, RUMG, RMG, ROPH, RCMC, RCH, ROPPG, RCMG, RU
- **Content Hashing**: SHA-256 for differential sync (only re-process changed docs)
- **Hierarchical Chunking**: 3 levels (document → section → semantic)

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

### Ingestion Report

After ingestion, you'll see a report like:

```
INGESTION REPORT
================
  Duration: 40.5 seconds
  Backend: docling

  Documents:
    Total: 10
    Successful: 10
    Failed: 0

  Chunks:
    Total created: 361
    Total uploaded: 361
    Avg per document: 36.1

  Metadata Extraction Quality:
    Title extracted: 10/10 (100%)
    Reference # extracted: 9/10 (90%)
    Applies To extracted: 9/10 (90%)

  Entity Boolean Extraction:
    Docs with entity booleans: 9/10 (90%)
    Total entity associations: 14
    Avg entities per doc: 1.4
```

## Azure AI Search Schema

The `rush-policies` index uses a 29-field schema:

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
| `content_hash` | String | MD5 hash for change detection |
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
  "knowledge_base": "rush-policies-kb",
  "agent_initialized": true
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
  "sources": [
    {
      "title": "Verbal and Telephone Orders",
      "reference_number": "486",
      "section": "II. Policy",
      "applies_to": "RUMC",
      "date_updated": "02/02/2024"
    }
  ]
}
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI application entrypoint |
| `app/services/foundry_agent.py` | Azure AI Foundry AgentsClient wrapper |
| `app/services/chat_service.py` | Chat orchestration logic |
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

To add more origins, update `allow_origins` in `main.py`.

## Troubleshooting

### Agent Issues
- **Agent not found**: Run `python scripts/create_foundry_agent.py` from project root
- **Connection errors**: Verify `AZURE_AI_PROJECT_ENDPOINT` is correct

### Ingestion Issues
- **Docling not found**: Install with `pip install docling docling-core`
- **Empty chunks**: Check PDF is not scanned (OCR not enabled)
- **Missing metadata**: Check header table format matches expected patterns

### Search Issues
- **No results**: Verify index has documents (`python azure_policy_index.py stats`)
- **Low relevance**: Ensure `query_type` is `VECTOR_SEMANTIC_HYBRID`

## API Documentation

When the server is running:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
