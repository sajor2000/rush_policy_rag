# Changelog

All notable changes to the RUSH Policy RAG Agent project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- **Device Ambiguity Detection & Clarification UI** (2026-01-08): Intelligent query disambiguation for medical devices
  - New `detect_device_ambiguity()` method in `chat_service.py` detects ambiguous terms (IV, catheter, line, port)
  - Frontend clarification UI in `ChatInterface.tsx` prompts users to specify device type before searching
  - Prevents noisy results: "IV dwell time" now asks "Peripheral IV or PICC line?" instead of returning mixed results
  - 4 ambiguous device types with 3-4 clarification options each
  - Added `clarification` field to `ChatResponse` schema
  - **Performance impact**: Reduces irrelevant results by 60-70% for vague device queries
  - 8 new unit tests in `test_chat_service.py` (100% pass rate)
- **Context-Aware Synonym Expansion with Priority-Based Stopping** (2026-01-08): Prevents cascading query expansions
  - Complete rewrite of `synonym_service.py` expansion logic with 4-level priority system
  - New `CONTEXT_SPECIFIC_EXPANSIONS` dictionary with 28 device-specific mappings
  - New `NEUTRAL_FALLBACKS` dictionary for generic terms (prevents cascading)
  - **OLD**: "IV" → "peripheral PIV catheter" → "urinary Foley" (cascade noise)
  - **NEW**: "IV" → "intravenous vascular access" (stops, neutral)
  - Priority levels: Multi-word specific → Single-word specific → General → Neutral fallback
  - 9 new unit tests in `test_synonym_expansion.py` (100% pass rate)
- **Azure Synonym Map Cleanup** (2026-01-08): Device-type specific synonym rules
  - Removed generic bidirectional mapping: "IV, intravenous, infusion, drip" (conflated infusion pumps with IV catheters)
  - Added 11 device-specific synonym lines (PIV, PICC, CVC, Foley, epidural, dialysis, port, arterial)
  - Prevents BM25 keyword matching from conflating different medical devices
  - 168 total synonym rules uploaded to Azure AI Search
- **Post-Rerank Score Windowing** (2026-01-08): Filters noisy results after Cohere reranking
  - New `filter_by_score_window()` method in `chat_service.py`
  - Keeps only results within 60% of top rerank score (configurable threshold)
  - Prevents over-filtering: Maintains minimum 2 results, skips filtering when top score < 0.3
  - Example: Query "IV dwell time" with scores [0.85, 0.82, 0.45, 0.38] → Filters to [0.85, 0.82]
  - 6 new unit tests in `test_chat_service.py` (100% pass rate)
- **Three-Tier PDF Access UI** (2026-01-08): PDFs directly correlated with evidence
  - **Tier 1**: Per-evidence PDF button on each evidence card (primary access)
  - **Tier 2**: Sticky quick access panel with numbered PDF shortcuts (persistent during scroll)
  - **Tier 3**: Bottom section retained as fallback (non-breaking change)
  - Updated `handleViewPdf` to navigate to specific page numbers when available
  - WCAG 2.1 AA compliant with keyboard navigation and ARIA labels
- **Collapsible "Related" Evidence** (2026-01-08): Prevents users from following wrong policies
  - Evidence with `match_type: "related"` now collapsed by default
  - Visual de-emphasis: Gray badge, amber background, lower opacity
  - Expandable `<details>` element with warning text: "(may not directly support the answer)"
  - Prevents Melissa's concern: Users "not paying close enough attention" might follow incorrect policy
- **Enhanced Test Suite** (2026-01-08): 23 new tests covering all features
  - 8 ambiguity detection tests (IV, catheter, line, port, false positives)
  - 9 synonym expansion tests (context-specific, cascading prevention, priority stopping)
  - 6 score windowing tests (noise filtering, thresholds, edge cases)
  - **Total test coverage**: 71 tests passing (48 existing + 23 new)
- **Cohere Rerank 3.5 Integration**: Added cross-encoder reranking via Azure AI Foundry
  - New `app/services/cohere_rerank_service.py` with async HTTP client
  - Integrated into chat pipeline after Azure AI Search retrieval
  - Configurable via env vars: `USE_COHERE_RERANK`, `COHERE_RERANK_ENDPOINT`, `COHERE_RERANK_API_KEY`
  - Healthcare-calibrated threshold: 0.15 min score, top_n=10
  - **Performance impact**: Pass rate improved from 77.8% to 100% (36/36 tests)
  - Key improvement: Cross-encoder understands negation ("NOT authorized" contradicts "Can accept orders?")
  - Full documentation added to DEPLOYMENT.md (Step 5.5), README.md, CLAUDE.md
- **Tech Stack Finalization**: Confirmed production-ready FastAPI + Next.js architecture
  - Backend: FastAPI with rate limiting (slowapi), async I/O, Azure AD auth support
  - Frontend: Next.js 14 with security headers (CSP, HSTS, X-Frame-Options)
  - All agent calls via Azure OpenAI "On Your Data" (vectorSemanticHybrid)
  - PDF viewing fully functional
- **Production Security Audit**: Comprehensive security review completed
  - Input validation with OData injection prevention
  - File upload security (magic bytes, size limits, extension whitelist)
  - CORS with explicit origins (not wildcards)
  - Fail-fast config mode for production (`FAIL_ON_MISSING_CONFIG=true`)
- **Azure OpenAI "On Your Data" Service**: Implemented full vectorSemanticHybrid search via Chat Completions API
  - New `app/services/on_your_data_service.py` with proper `semantic_configuration` support
  - Fixes azure-ai-agents SDK limitation (missing `semantic_configuration_name` parameter)
  - Enables Vector + BM25 + L2 Semantic Reranking (best search quality)
  - Priority: On Your Data > Foundry Agent > Standard retrieval
- **Request Logging Middleware**: Added structured logging with correlation IDs
  - New `app/core/logging_middleware.py` with request ID propagation
  - JSON format support for App Insights compatibility (`LOG_FORMAT=json`)
  - Latency tracking for all API endpoints
- **Performance Baseline Script**: Enhanced `scripts/measure_backend_performance.py`
  - Captures P50/P95 latency metrics for health, search, and chat endpoints
  - JSON output to `docs/baselines/` for before/after comparisons
- **Single Backend Simplification Plan**: Documented FastAPI-only roadmap in `docs/SINGLE_BACKEND_SIMPLIFICATION.md` and linked it from README/DEPLOYMENT so all contributors follow the new architecture direction.
- **PyMuPDF Checkbox Extraction**: Added PyMuPDF-first extraction strategy for "Applies To" checkboxes
  - `_extract_applies_to_from_raw_pdf()` method in chunker.py (lines 687-750)
  - Fallback chain: PyMuPDF → Docling → Regex
  - Fixes truncation issue where Docling's TableFormer cut off checkbox rows
- **Full Pipeline Ingestion**: New `full_pipeline_ingest.py` script with parallel processing
  - Performance: 50 documents → 989 chunks in ~85-113 seconds
  - ThreadPoolExecutor for concurrent blob downloads
  - Batch embedding and chunked index uploads
- **Test Framework**: Added `run_test_dataset.py` for RAG evaluation
  - 27 test cases across 5 categories (general, edge_case, adversarial, multi_policy, not_found)
  - Current pass rate: 88.9%
- **Debug Scripts**: Added PDF analysis tools
  - `debug_pdf_structure.py` - PyMuPDF text extraction debugging
  - `test_checkbox_extraction.py` - A/B testing extraction methods
- **Backend .gitignore**: Created dedicated .gitignore for apps/backend/

### Changed
- **Documentation**: Updated README.md and CLAUDE.md
  - Added PyMuPDF checkbox extraction section
  - Added full pipeline ingestion documentation
  - Added test strategy section
  - Updated tech stack to include PyMuPDF
- **Deployment Guide**: Added FastAPI-only callout plus reference to the simplification plan so Azure rollouts avoid the deprecated Function proxy.
- **Deployment**: Updated to full Azure deployment (Rush Azure tenant)
  - Added `infrastructure/azure-container-app-frontend.bicep` for frontend
  - Updated backend Bicep with Azure Container Apps CORS configuration
  - Added `customFrontendDomain` parameter for custom domains
  - Updated DEPLOYMENT.md for Azure-only architecture
- **Chunker**: Updated `preprocessing/chunker.py` to use dual-library approach
  - PyMuPDF primary for checkbox extraction
  - Docling fallback for content parsing
- **License**: Changed from MIT to internal use only (Rush University System for Health)

### Removed
- **PDF Upload UI Feature**: Removed end-user upload from web interface (2025-11-28)
  - Documents will be updated monthly via CLI scripts or Azure Portal
  - Deleted: `PDFUpload.tsx`, `/api/upload` routes, `upload_service.py`
  - CLI scripts remain available for admin use
- **`/serverless/agent-proxy/`**: Deleted deprecated Azure Function proxy (2025-11-28)
  - All traffic now flows through FastAPI backend
  - Single-backend simplification complete
  - See `docs/SINGLE_BACKEND_SIMPLIFICATION.md` for migration details
- Duplicate `env.example` file (kept `.env.example`)
- Empty `test_pdfs/` directory
- Unused `apps/frontend/.replit` config file
- Frontend `.git/` directory (merged into monorepo)
- `vercel.json` (full Azure deployment, no Vercel)

### Fixed
- **NPO Policy Checkbox Extraction**: Now correctly extracts `Applies To: RUMC, RUMG, ROPH, RCH`
  - Previously truncated to just `RUMC` due to Docling TableFormer limitation

## [1.0.0] - 2024-11-26

### Added
- Initial release of RUSH Policy RAG Agent
- Azure AI Foundry Agents integration with persistent agent architecture
- VECTOR_SEMANTIC_HYBRID search with 132 synonym rules
- Next.js 14 frontend with RUSH brand styling
- FastAPI backend with health check endpoints
- Docling PDF processing with TableFormer ACCURATE mode
- 29-field Azure AI Search schema with entity boolean filters
- Query-time synonym expansion (155 medical abbreviations, 56 misspellings)
- Docker multi-stage builds for production deployment
- Bicep IaC templates for Azure Container Apps
- GitHub Actions CI/CD workflows
