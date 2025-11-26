# Changelog

All notable changes to the RUSH Policy RAG Agent project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
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
