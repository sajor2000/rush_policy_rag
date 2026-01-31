# Scripts Reference

This is a map of the repo scripts and what they do. Unless noted, run from the repo root.
Many scripts expect a `.env` file at the repo root (see `docs/ENV_VARS.md`).

## Local Dev and Smoke Tests

| Script | Purpose | Notes |
| --- | --- | --- |
| `start_backend.sh` | Create venv, install backend deps, run FastAPI | macOS/Linux |
| `start_backend.ps1` | Same as `start_backend.sh` | Windows PowerShell |
| `start_frontend.sh` | Install frontend deps and run Next.js dev server | macOS/Linux |
| `start_frontend.ps1` | Same as `start_frontend.sh` | Windows PowerShell |
| `scripts/test_local.sh` | Validate env + run local health checks | Uses `BACKEND_URL` |
| `scripts/healthcheck.sh` | Run a Docker image and hit `/health` | `./scripts/healthcheck.sh <image> [env_file]` |

## Deployment and Azure (Shell)

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/deploy/aca_provision.sh` | Create/verify resource group and ACA environment | Container Apps only |
| `scripts/deploy/aca_deploy.sh` | Create/update a Container App | Auto-loads `.env` if present |
| `scripts/deploy/build_push.sh` | Docker build + push to ACR | Uses local Docker |
| `scripts/deploy/set-env-vars.sh` | Update env vars for Container Apps or Web App | Supports `.env` conversion |
| `scripts/deploy/set_secrets.sh` | Store secrets and link env vars in ACA | Uses `secretref:` |
| `scripts/deploy/assign_roles.sh` | Assign managed identity roles | Needs storage/search scopes |
| `scripts/deploy/convert-env-to-azure.sh` | Convert `.env` into Azure CLI args | Used by other scripts |
| `scripts/deploy/common-functions.sh` | Shared helpers | Not run directly |
| `scripts/deploy/webapp_containers_deploy.sh` | Deploy to Azure Web App for Containers | Legacy path |
| `scripts/deploy/create-zip-deploy.sh` | Build ZIP deploy package | Legacy path |
| `scripts/deploy/fix-zip-deploy.sh` | Patch ZIP deploy issues | Legacy path |
| `scripts/deploy/analyze-zip-deploy-issues.sh` | Diagnose ZIP deploy failures | Legacy path |
| `scripts/deploy-to-azure.sh` | Bicep deploy (GHCR image) | Alternate/legacy path |
| `scripts/setup-production.sh` | Provision Azure resources | Alternate/legacy path |

## Ingestion and Indexing

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/full_pipeline_ingest.py` | End-to-end ingest with timing | Clears index by default |
| `scripts/checkpointed_pipeline.py` | Resumable ingest with checkpoints | Supports `--resume` |
| `scripts/rebuild_index.py` | Rebuild search index + synonym map | One-time migrations |
| `scripts/mark_v1_baseline.py` | Set version baseline on blobs + chunks | Run once |
| `scripts/upload_pdfs_to_blob.py` | Upload PDFs to `policies-active` | Enables PDF viewer |
| `apps/backend/scripts/ingest_all_policies.py` | Ingest from blob or local folder | Primary ingest tool |
| `apps/backend/scripts/reindex_specific_files.py` | Reindex named PDFs | Targeted updates |
| `apps/backend/policy_sync.py` | Detect/sync changes between blob containers | Run from `apps/backend/` |

## Evaluation and QA

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/run_test_dataset.py` | Run local test dataset against API | Uses `apps/backend/data/test_dataset.json` |
| `scripts/run_evaluation.py` | Run Azure + RAGAS evaluators | Exports JSON/CSV |
| `scripts/run_enhanced_evaluation.py` | Enhanced eval suite (cohere/hallucination/risen) | Category flags |
| `scripts/run_agent_evaluation.py` | Azure AI evaluators for reliability | Multi-mode |
| `scripts/run_garak_adversarial.py` | Adversarial/jailbreak testing | Optional for security |
| `scripts/audit_evaluation_failures.py` | Classify evaluation failures | Evaluator vs RAG |
| `scripts/review_responses.py` | Human review CLI for results | Interactive |
| `scripts/generate_test_dataset.py` | Generate test cases from PDFs | Offline |
| `scripts/generate_ceo_report.py` | Executive QA report | Optional |

## Debugging and Utilities

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/debug_pdf_structure.py` | Inspect PDF checkbox layout | PyMuPDF |
| `scripts/test_checkbox_extraction.py` | A/B checkbox extraction methods | Compares Docling vs pypdf |
| `scripts/measure_backend_performance.py` | Latency baselines for endpoints | Uses `BACKEND_URL` |
| `scripts/ssl_fix.py` | SSL fix for corporate proxy | Import first in scripts |

## Backend Scripts Folder (`apps/backend/scripts/`)

These are executed from `apps/backend/`:

| Script | Purpose |
| --- | --- |
| `setup_azure_infrastructure.py` | Provision Azure services (search, storage, etc.) |
| `ingest_all_policies.py` | Ingest from blob/local PDFs into Azure Search |
| `reindex_specific_files.py` | Reindex a subset of PDFs |
