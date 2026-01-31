# New Engineer Onboarding

This is the start-here guide for taking over the RUSH Policy RAG Agent repo.

## 1. Quick Start (Local)

1) Create your env file at the repo root:

```bash
cp .env.example .env
```

2) Start the backend (terminal 1):

```bash
./start_backend.sh
```

3) Start the frontend (terminal 2):

```bash
./start_frontend.sh
```

4) Open the UI:

```
http://localhost:3000
```

## 2. Repo Map

- `apps/backend/` - FastAPI backend, ingestion pipeline, and evaluation code
- `apps/frontend/` - Next.js frontend (App Router)
- `scripts/` - deployment, ingestion, QA, and utility scripts
- `docs/` - documentation hub (see `docs/README.md`)
- `infrastructure/` - Azure Bicep templates

## 3. App Entry Points

Backend:
- `apps/backend/main.py` - FastAPI app instance (`app`) and middleware setup
- `apps/backend/app/api/routes/*.py` - API routes (`/api/chat`, `/api/search-instances`, `/api/pdf`, `/api/admin`)

Frontend:
- `apps/frontend/src/app/page.tsx` - main UI entry point
- `apps/frontend/src/app/layout.tsx` - root layout/providers
- `apps/frontend/src/app/api/**/route.ts` - Next.js API routes (proxy to backend)

## 4. Local Dev Notes

- Backend loads `.env` from the repo root (see `apps/backend/app/core/config.py`).
- Frontend reads `apps/frontend/.env.local` if present. Set `BACKEND_URL` for local or deployed API.
- Script reference: `docs/SCRIPTS.md`.

## 5. Deploy to Azure from the IDE (VS Code)

These are the same CLI steps in `DEPLOYMENT.md`, run from your IDE terminal.

1) Open the repo in VS Code.
2) Terminal -> New Terminal (repo root).
3) Login and select the subscription:

```bash
az login
az account set --subscription "RU-Azure-NonProd"
```

4) Build and push the backend image to ACR:

```bash
az acr build --registry aiinnovation --image policytech-backend:latest ./apps/backend
```

5) Update the backend Container App:

```bash
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-backend:latest
```

6) Get the backend URL for the frontend build:

```bash
BACKEND_URL=$(az containerapp show \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --query properties.configuration.ingress.fqdn -o tsv)
```

7) Build and push the frontend image:

```bash
az acr build \
  --registry aiinnovation \
  --image policytech-frontend:latest \
  --build-arg BACKEND_URL="https://$BACKEND_URL" \
  ./apps/frontend
```

8) Update the frontend Container App:

```bash
az containerapp update \
  --name rush-policy-frontend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/policytech-frontend:latest
```

9) Verify:

```bash
curl "https://$BACKEND_URL/health"
```

First-time provisioning (resource creation, secrets, etc.) is in `DEPLOYMENT.md`.

## 6. Where to Go Next

- `docs/SCRIPTS.md` - what each script does
- `docs/ENV_VARS.md` - environment variables
- `DEPLOYMENT.md` - full Azure setup and deployment
- `docs/TROUBLESHOOTING.md` - common issues
