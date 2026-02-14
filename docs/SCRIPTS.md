# Scripts Reference

This is a map of the repo scripts and what they do. Unless noted, run from the repo root.
Many scripts expect a `.env` file at the repo root (see `docs/ENV_VARS.md`).

## Local Dev and Smoke Tests

| Script | Purpose | Notes |
| --- | --- | --- |
| `start_backend.sh` | Create venv, install backend deps, run FastAPI | macOS/Linux |
| `start_frontend.sh` | Install frontend deps and run Next.js dev server | macOS/Linux |
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
| `scripts/deploy-to-azure.sh` | Bicep deploy (ACR image) | Alternate/legacy path |
| `scripts/setup-production.sh` | Provision Azure resources | Alternate/legacy path |

## Ingestion and Indexing

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/full_pipeline_ingest.py` | End-to-end ingest with timing | **Recommended** - clears index by default |
| `scripts/local_folder_ingest.py` | Ingest from local folder | Alternative to full pipeline |
| `scripts/upload_pdfs_to_blob.py` | Upload PDFs to `policies-active` | Enables PDF viewer |
| `apps/backend/scripts/ingest_all_policies.py` | Ingest from blob or local folder | Primary ingest tool |
| `apps/backend/scripts/reindex_specific_files.py` | Reindex named PDFs | Targeted updates |
| `apps/backend/policy_sync.py` | Detect/sync changes between blob containers | Run from `apps/backend/` |

## Evaluation and QA

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/run_test_dataset.py` | Run local test dataset against API | Uses `apps/backend/data/test_dataset.json` |
| `scripts/run_enhanced_evaluation.py` | Enhanced eval suite (cohere/hallucination/risen) | **Recommended** - category flags |
| `scripts/audit_evaluation_failures.py` | Classify evaluation failures | Evaluator vs RAG |
| `scripts/weekly_eval.py` | Weekly production evaluation | Email reports |
| `scripts/generate_executive_report.py` | Executive usage report | AI-powered question classification |
| `scripts/generate_test_dataset_v5.py` | Generate test cases from PDFs | Latest version |
| `scripts/generate_test_dataset_from_pdfs.py` | Alternative test dataset generator | Uses PDF content |
| `scripts/integrate_realistic_questions.py` | Integrate realistic staff questions | 100 production tests |
| `scripts/run_ragas_regression.py` | RAGAS v0.4 regression against golden test set | Before/after delta comparison |
| `scripts/run_combined_weekly_report.py` | Combined technical + executive weekly email | Merges DeepEval + usage |
| `scripts/generate_pre_deployment_audit.py` | Pre-deployment audit report (HTML+JSON) | 10-section auditor report |

## Monthly Operations

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/monthly_bulk_sync.py` | Bulk sync for monthly policy updates | Batch operations |
| `scripts/optimized_batch_ingest.py` | Performance-optimized batch ingestion | Parallelized |
| `scripts/audit_drift_report.py` | Month-over-month audit drift detection | See [AUDIT_AND_QUALITY.md](AUDIT_AND_QUALITY.md) |
| `scripts/persist_evaluation_baseline.py` | Save evaluation scores per release to Azure Blob | Called by monthly release gate |
| `scripts/audit_lifecycle_cleanup.py` | Enforce 90-day audit retention policy | `--dry-run` to preview |

## PromptFoo Compliance

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/generate_promptfoo_dataset.py` | Generate PromptFoo test dataset | Compliance auditing |
| `scripts/promptfoo_sync_dataset.py` | Sync dataset for PromptFoo eval | Keeps tests current |

## Security

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/security_audit.py` | Run security audit checks | Pre-deployment |
| `scripts/validate_fixes.py` | Validate applied security fixes | Post-fix verification |

## Debugging and Utilities

| Script | Purpose | Notes |
| --- | --- | --- |
| `scripts/debug_pdf_structure.py` | Inspect PDF checkbox layout | PyMuPDF |
| `scripts/test_checkbox_extraction.py` | A/B checkbox extraction methods | Compares Docling vs pypdf |
| `scripts/validate_metadata_extraction.py` | Validate PDF metadata extraction | Quality check |
| `scripts/validate_bug_fix_ingestion.py` | Pre-reindex BUG-001/002/003 fix validation | Downloads 3 PDFs, verifies metadata + content prefix |
| `scripts/audit_quality.py` | Audit ingestion quality | Post-ingest validation |
| `scripts/ssl_fix.py` | SSL fix for corporate proxy | Import first in scripts |
| `scripts/detect_mac_hardware.py` | Detect macOS hardware for optimization | Apple Silicon detection |

## Backend Scripts Folder (`apps/backend/scripts/`)

These are executed from `apps/backend/`:

| Script | Purpose |
| --- | --- |
| `setup_azure_infrastructure.py` | Provision Azure services (search, storage, etc.) |
| `ingest_all_policies.py` | Ingest from blob/local PDFs into Azure Search |
| `reindex_specific_files.py` | Reindex a subset of PDFs |
| `ingest_with_checkpoints.py` | Checkpoint-based resumable ingestion |
| `optimized_ingest.py` | Performance-optimized ingestion |
| `blue_green_index_alias.py` | Blue/green index alias swap for zero-downtime updates |
| `audit_ingestion_quality.py` | Post-ingestion quality audit |
| `diagnose_multipage_bug.py` | Diagnose multi-page chunking issues |
| `monthly_hr_release_gate.py` | Monthly HR release gate checks |
| `verify_hr_retrieval_regressions.py` | Verify HR retrieval regression tests pass |

