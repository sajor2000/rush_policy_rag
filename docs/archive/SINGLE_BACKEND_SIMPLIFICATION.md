# Single-Backend Simplification Plan

## STATUS: ✅ COMPLETED (2025-11-28)

The RUSH Policy RAG stack has been successfully simplified to a **single FastAPI backend + Next.js frontend** architecture. All Azure Functions have been removed.

---

## Final Architecture

```
Browser → Next.js Frontend (Port 3000) → FastAPI Backend (Port 8000) → Azure Services
                                                                        ├── Azure AI Search
                                                                        ├── Azure OpenAI
                                                                        └── Azure Blob Storage
```

**No Azure Functions** - Pure FastAPI + Next.js stack.

---

## Completion Summary

| Checkpoint | Status | Notes |
|------------|--------|-------|
| Repository references to Function proxy | ✅ Complete | `/serverless/agent-proxy` directory **deleted** on 2025-11-28 |
| Frontend API target | ✅ FastAPI via `BACKEND_URL` | All API calls proxy through Next.js to FastAPI |
| Auth/token handling parity | ✅ Azure AD enforced in FastAPI | `REQUIRE_AAD_AUTH` gates available |
| Observability/performance baselines | ✅ Complete | Baseline captured 2025-11-26 |
| Security headers | ✅ Complete | CSP, HSTS, X-Frame-Options configured |
| Rate limiting | ✅ Complete | slowapi with 30/min limits |
| Async I/O | ✅ Complete | azure.storage.blob.aio, asyncio.to_thread |
| Production readiness audit | ✅ Complete | Backend 95%, Frontend 95% |

**Deleted on 2025-11-28:**
- `/serverless/agent-proxy/` (entire directory)

## 1. Retire the Azure Function Agent Proxy

| Goal | Ensure all chat traffic flows directly through FastAPI (`apps/backend`) so we can delete `/serverless/agent-proxy` without losing functionality. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Actions**
1. Search for any runtime references to `agent/messages`, `AZURE_AI_PROJECT_AGENT_ID`, or `serverless/agent-proxy` in the frontend (`apps/frontend`) and scripts (`scripts/`, `start_*.sh`).
2. Confirm the frontend uses `BACKEND_URL` (FastAPI) for both chat and PDF download; update `src/lib/api.ts` (or equivalent service wrappers) if any Function URL remains.
3. Update IaC (Bicep templates under `infrastructure/`) and deployment docs to remove the Function App resource.
4. After validation, delete `/serverless/agent-proxy` and related Azure resources.

**Risks & Mitigations**
- *Azure AD On-Behalf-Of flow*: If any caller relied on the Function App to swap tokens, replicate the requirement via FastAPI middleware or document why it is no longer needed.
- *CORS*: Re-validate `settings.CORS_ORIGINS` in `app/core/config.py` to ensure equivalent coverage once the proxy is gone.

**Success Criteria**
- Frontend chat + PDF viewer work with FastAPI-only endpoint in local, staging, and production.
- No references to `serverless/agent-proxy` remain in the repository.
- Azure resource inventory contains only the Container App/App Service for FastAPI.

## 2. Consolidate Configuration Management

| Goal | Make `.env` the single source of truth for both local and Azure deployments while exposing typed settings via `app/core/config.py`. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Actions**
1. Inventory all environment variables used by backend scripts (`policy_sync.py`, `scripts/*.py`), `start_backend.sh`, and FastAPI settings.
2. Add missing fields to `Settings` in `app/core/config.py` (e.g., `FOUNDRY_AGENT_ID`, storage vars) so every reader imports the same object instead of re-reading `os.environ`.
3. Extract a helper (e.g., `app/core/env.py`) that loads `.env` once for scripts; refactor CLI scripts to `from app.core.config import settings` instead of `dotenv.load_dotenv` duplicates.
4. For Azure deployments, document how secrets map to settings (Container Apps `secretref:*` and App Service app settings) in `DEPLOYMENT.md`.

**Deliverables**
- Updated `config.py` with complete schema + helpful warnings.
- Shared helper module for scripts plus refactors to high-traffic scripts (`scripts/create_foundry_agent.py`, `apps/backend/scripts/*.py`).

## 3. Streamline Backend Ingestion & PDF Pipeline

| Goal | Reduce duplication between `policy_sync.py`, `preprocessing/chunker.py`, and ingestion scripts so there is one clear entry point per workflow. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Actions**
1. Document the canonical ingestion steps in this file and mirror them in `README.md` (initial ingest) + `DEPLOYMENT.md` (operations playbook).
2. Extract shared chunking helpers (checkbox extraction, Docling fallbacks) into `preprocessing/chunker.py` and make CLI scripts import them instead of embedding logic.
3. Collapse legacy/unused scripts under `preprocessing/archive/` or delete them once parity is proven.
4. Standardize logging (`logging.getLogger(__name__)`) and exception handling so CLI exits are predictable for automation.

**Success Criteria**
- One entry point for each operator task: `scripts/ingest_all_policies.py` (full ingest) and `policy_sync.py` (incremental).
- Checkbox extraction + metadata enrichers only live in `preprocessing/chunker.py`.

## 4. Align Frontend ↔️ Backend Contracts

| Goal | Ensure the frontend talks only to FastAPI using a typed client so endpoint changes remain localized. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Actions**
1. Centralize API calls inside `apps/frontend/src/lib/api.ts` (or create it) with functions like `postChat()` and `getPdfUrl()`.
2. Update components/pages (`src/app/chat/page.tsx`, PDF viewer components) to call those helpers, removing any hard-coded URLs or fetch logic.
3. Add TypeScript interfaces that mirror FastAPI response models (e.g., `ChatResponse`, `Citation` from `app/api/routes/chat.py`).
4. Extend the frontend `.env` template to require only `BACKEND_URL`, making it obvious that FastAPI is the single gateway.

**Success Criteria**
- Changing the backend URL requires editing only `.env` + possibly `next.config.mjs`.
- There is zero mention of the Azure Function endpoint in the frontend codebase.

## 5. Documentation Updates

| Goal | Reflect the simplified architecture everywhere so new contributors follow the FastAPI-only path by default. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Actions**
1. `README.md`: add a "Single Backend Initiative" callout linking to this plan and highlighting the removal of the Function proxy.
2. `DEPLOYMENT.md`: update the architecture diagram description + steps to omit Function App creation, and add a checklist for confirming FastAPI-only deployments.
3. `docs/CHANGELOG.md`: log the simplification decision for traceability.
4. Remove or archive any doc that still references the proxy (e.g., `APP_COST.md` sections, if applicable).

**Success Criteria**
- Running `rg "agent-proxy"` or `rg "Function App"` across `*.md` yields zero matches (other than historical changelog entries).
- Onboarding instructions mention only FastAPI + Next.js deployments.

## 6. Validation & Rollout Checklist

| Goal | Guard against regressions as we remove infrastructure and refactor ingestion. |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|

**Checks to Automate/Run**
1. **Backend unit tests**: `cd apps/backend && python -m pytest -m "not slow"` (or `tests/test_synonym_service.py` as a smoke test) on every PR touching backend code.
2. **API contract tests**: add/extend `apps/backend/tests/test_queries.py` to cover `/api/chat` success + failure paths.
3. **Frontend lint/typecheck**: `cd apps/frontend && npm run lint && npm run check` in CI to ensure API clients stay typed.
4. **Deployment probe**: after Azure deploy, run `curl https://<backend>/health` and a scripted chat request hitting FastAPI directly.
5. **Blob/PDF validation**: automated job invoking `python policy_sync.py detect` nightly to confirm storage/index drift.

**Rollout Steps**
1. Merge code/config/docs changes into `main`.
2. Deploy FastAPI container to staging, point frontend `BACKEND_URL` to it, and complete chat/PDF smoke tests.
3. Delete/stage-down the Azure Function App after one stable week; update monitoring alerts accordingly.
4. Communicate the change in release notes and notify stakeholders that only the FastAPI endpoint should be used going forward.

---

**Owner**: RUSH Policy RAG maintainers

**Last Updated**: 2025-11-26
