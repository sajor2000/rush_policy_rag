# RUSH Policy RAG Agent — Technical Architecture (PwC)

This document summarizes the current end-to-end architecture, tech stack, and implemented capabilities of the RUSH Policy RAG Agent (frontend + backend + Azure services) for technical architecture review.

## 1) Scope & Goals

**Primary goal:** Enable staff to ask natural-language questions and receive **RUSH-policy-grounded** answers with **verbatim supporting evidence** and **source PDF access**.

**Non-goals:** Clinical guidance outside RUSH policies; replacing PolicyTech as the system of record.

## 2) Runtime Architecture (Query Path)

```mermaid
flowchart LR
  U[User] -->|HTTPS| FE[Next.js (UI + Route Handlers)<br/>apps/frontend]
  FE -->|POST /api/chat| BE[FastAPI API<br/>apps/backend]
  FE -->|POST /api/search-instances| BE
  FE -->|GET /api/pdf/*| BE

  subgraph BE_SVC[Backend Services]
    CS[ChatService<br/>RAG orchestration]
    SS[SynonymService<br/>query expansion]
    PDFS[PDF SAS URL service<br/>Azure Blob Storage]
  end

  BE --> CS
  CS --> SS
  CS -->|Hybrid retrieval| AIS[Azure AI Search<br/>index: rush-policies]
  CS -->|Optional rerank| COH[Cohere Rerank 3.5<br/>(Azure AI Foundry)]
  CS -->|Generate answer| AOAI[Azure OpenAI<br/>GPT-4.1]
  PDFS --> BLOB[Azure Blob Storage<br/>policies-active (PDFs)]

  BE -->|Citations + evidence + sources| FE
  FE -->|Evidence cards + PDF viewer| U
```

### Key runtime behaviors

- **Frontend** renders:
  - A **Quick Answer** (model-generated summary).
  - **Supporting Evidence** cards containing verbatim policy snippets and metadata (title, ref #, section, page).
  - A **PDF viewer** that loads secure, time-limited PDF URLs from the backend.
- **Backend** enforces:
  - Input validation (`validate_query`) and rate limiting (`slowapi`).
  - Circuit breaker behavior for Azure OpenAI outages/timeouts.
  - Strict “policy-grounded only” prompting (`RISEN_PROMPT`), with special handling for not-found/refusal cases.

## 3) Data / Ingestion Architecture (Policy PDFs → Search Index)

```mermaid
flowchart LR
  SRC[Policy PDFs] -->|Upload| B1[Azure Blob Storage<br/>policies-source]
  B1 -->|Download| CH[Docling-based chunker<br/>apps/backend/preprocessing/chunker.py]
  CH -->|Extract metadata + text| PCH[PolicyChunk records<br/>verbatim text + citation metadata]
  PCH -->|Embeddings (3072-d)| EMB[text-embedding-3-large]
  PCH -->|Upload| AIS[Azure AI Search<br/>rush-policies index]
  AIS -->|Query-time retrieval| RAG[RAG Query Pipeline]
  B1 -->|Promote on success| B2[Azure Blob Storage<br/>policies-active]
  B1 -->|Archive on delete| B3[Azure Blob Storage<br/>policies-archive]
```

### What is indexed (high level)

- **Verbatim chunk text** (searchable) and **vector embeddings** (semantic).
- Policy metadata: `title`, `reference_number`, `section`, `citation`, `date_updated`, `source_file`, etc.
- **Entity filters**: `applies_to_{rumc,rumg,rmg,roph,rcmc,rch,roppg,rcmg,ru}`.
- `page_number` is included to support “jump to page” UX in the PDF viewer.

## 4) API Surface (Backend)

### Browser-facing (Next.js route handlers)

- `POST /api/chat` — proxies to backend chat (same-origin for the browser)
- `POST /api/search-instances` — proxies within-policy search for “Search” UX
- `GET /api/pdf/<path>` — proxies to backend PDF SAS URL generation

### Backend (FastAPI)

- `POST /api/v1/chat` (and legacy `POST /api/chat`) — main RAG Q&A endpoint (returns summary + evidence + sources)
- `POST /api/v1/search` (and legacy `POST /api/search`) — raw search results (debug/utility)
- `POST /api/v1/search-instances` (and legacy `POST /api/search-instances`) — within-policy search (instance/section search)
- `GET /api/v1/pdf/{filename}` (and legacy `GET /api/pdf/{filename}`) — returns a time-limited, read-only SAS URL for a PDF in blob storage
- `GET /health` — service health endpoint

## 5) Tech Stack

### Frontend (`apps/frontend`)

| Area | Tech |
|---|---|
| Framework | Next.js (App Router) + React 18 |
| Language | TypeScript |
| UI | Tailwind CSS + Radix UI primitives |
| Data fetching | `@tanstack/react-query` (client-side) |
| PDF viewing | `react-pdf` (PDF.js worker shipped in app) |
| UX features | Evidence cards, inline citation navigation, “search within policy” modal |

### Backend (`apps/backend`)

| Area | Tech |
|---|---|
| Framework | FastAPI + Uvicorn |
| Language | Python |
| Retrieval | Azure AI Search (`azure-search-documents`) hybrid (keyword + vector + semantic rank) |
| Generation | Azure OpenAI (GPT-4.1) |
| Reranking (optional) | Cohere Rerank 3.5 (Azure AI Foundry deployment) |
| Query expansion | Custom SynonymService + Azure AI Search synonym map |
| PDF processing | IBM Docling + PyMuPDF |
| Security | Azure AD claim dependency, input validation, rate limiting, circuit breaker |
| Observability | OpenTelemetry instrumentation + Azure Monitor exporter; Prometheus instrumentator |
| Testing | Pytest (+ asyncio) |

### Cloud / Deployment

| Area | Tech |
|---|---|
| Containers | Docker (backend + frontend) |
| Hosting | Azure Container Apps |
| Images | Azure Container Registry |
| Storage | Azure Blob Storage (source/active/archive containers) |
| Search | Azure AI Search (`rush-policies` index) |

## 6) What We’ve Implemented (to date)

### RAG query pipeline

- Hybrid retrieval from **Azure AI Search** (keyword + vector) with semantic ranking.
- Optional cross-encoder reranking via **Cohere Rerank 3.5** to improve precision and handle negation.
- Deterministic generation settings (e.g., `temperature=0.0`) and strict prompting to reduce hallucinations.
- Response payload includes:
  - `summary` / `response` text
  - `evidence[]` (verbatim snippets + metadata + page number)
  - `sources[]` (document-level source list)
  - `found` boolean to gate “not found” UX

### Evidence + PDF experience

- Evidence is rendered as **supporting cards** to keep the answer grounded and inspectable.
- PDFs are accessible via a backend-generated **time-limited SAS URL** (`/api/v1/pdf/{filename}`) for secure viewing.
- Frontend includes:
  - Citation navigation to jump between quick-answer citations and evidence cards
  - Search-within-policy modal (instance search workflow)

### Ingestion pipeline

- Automated processing of PDFs from blob storage, chunking into policy-aware sections, and indexing into Azure AI Search.
- Metadata extraction for:
  - Title, reference number, section labels
  - “Applies To” entity checkbox parsing into boolean filters
  - Page number support for PDF navigation

### Reliability, safety, and ops

- Rate limiting on public endpoints.
- Circuit breaker behavior for Azure OpenAI instability and explicit error mapping (429/504/503).
- Audit logging pipeline hooks for chat requests/responses and latency.
- OpenTelemetry + Azure Monitor support and Prometheus metrics instrumentation.

## 7) Key Code Locations (for review)

- Frontend chat UX: `apps/frontend/src/components/ChatInterface.tsx`
- Evidence rendering + citations + PDF links: `apps/frontend/src/components/ChatMessage.tsx`
- PDF viewer modal: `apps/frontend/src/components/PDFViewer.tsx`
- Chat endpoint + rate limiting + circuit breaker: `apps/backend/app/api/routes/chat.py`
- PDF SAS URL endpoint: `apps/backend/app/api/routes/pdf.py`
- RAG orchestration: `apps/backend/app/services/chat_service.py`
- Query expansion: `apps/backend/app/services/synonym_service.py`
- Chunking / ingestion: `apps/backend/preprocessing/chunker.py`
- Index schema / synonym map: `apps/backend/azure_policy_index.py`
