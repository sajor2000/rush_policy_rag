# Environment Variables Reference

> **Single Source of Truth** for all environment configuration in the RUSH Policy RAG system.
>
> Last Updated: 2026-01-11

## Quick Start

1. Copy `apps/backend/.env.example` to `apps/backend/.env`
2. Fill in required values (marked with *)
3. For frontend, create `apps/frontend/.env.local` with `BACKEND_URL`

---

## Backend Environment Variables

### Azure AI Search (Required*)

| Variable | Description | Example |
|----------|-------------|---------|
| `SEARCH_ENDPOINT`* | Azure AI Search service URL | `https://policychataisearch.search.windows.net` |
| `SEARCH_API_KEY` | Admin or query key (leave empty for DefaultAzureCredential) | `abc123...` |

### Azure OpenAI (Required*)

| Variable | Description | Example |
|----------|-------------|---------|
| `AOAI_ENDPOINT`* | Azure OpenAI resource URL | `https://myaoai.openai.azure.com/` |
| `AOAI_API_KEY`* | Azure OpenAI API key | `abc123...` |
| `AOAI_CHAT_DEPLOYMENT` | Chat model deployment name | `gpt-4.1` |
| `AOAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment | `text-embedding-3-large` |

> **Note**: Use `AOAI_API_KEY` not `AOAI_API`. The older `AOAI_API` is deprecated.

### Azure Blob Storage (Required*)

| Variable | Description | Example |
|----------|-------------|---------|
| `STORAGE_CONNECTION_STRING`* | Full connection string | `DefaultEndpointsProtocol=https;AccountName=...` |
| `SOURCE_CONTAINER_NAME` | Staging container for uploads | `policies-source` |
| `CONTAINER_NAME` | Production container | `policies-active` |

Alternative (for managed identity in production):

| Variable | Description | Example |
|----------|-------------|---------|
| `STORAGE_ACCOUNT_URL` | Account URL without credentials | `https://myaccount.blob.core.windows.net` |

### Cohere Rerank 3.5 (Required for Healthcare RAG*)

| Variable | Description | Example |
|----------|-------------|---------|
| `USE_COHERE_RERANK` | Enable Cohere reranking | `true` |
| `COHERE_RERANK_ENDPOINT`* | Azure AI Foundry deployment URL | `https://cohere.models.ai.azure.com` |
| `COHERE_RERANK_API_KEY`* | Cohere API key | `abc123...` |
| `COHERE_RERANK_MODEL` | Model name | `cohere-rerank-v3-5` |
| `COHERE_RERANK_TOP_N` | Documents to keep after rerank | `10` |
| `COHERE_RERANK_MIN_SCORE` | Minimum relevance threshold | `0.25` |

### Feature Flags

| Variable | Description | Default |
|----------|-------------|---------|
| `USE_ON_YOUR_DATA` | Enable Azure OpenAI "On Your Data" | `true` |
| `USE_COHERE_RERANK` | Enable Cohere cross-encoder | `true` |
| `FAIL_ON_MISSING_CONFIG` | Fail fast if config missing | `false` |

### Production Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `ADMIN_API_KEY`* | Protects `/api/admin/*` endpoints | `secure-random-key` |
| `CORS_ORIGINS` | Allowed origins (comma-separated) | `https://app.example.com` |
| `REQUIRE_AAD_AUTH` | Require Azure AD tokens | `false` |

### Azure AD Authentication (Optional)

| Variable | Description | Example |
|----------|-------------|---------|
| `REQUIRE_AAD_AUTH` | Enable Azure AD authentication | `false` |
| `AZURE_AD_TENANT_ID` | Azure AD tenant ID | `822ee4ca-...` |
| `AZURE_AD_CLIENT_ID` | App registration client ID | `abc123-...` |
| `AZURE_AD_TOKEN_AUDIENCE` | Expected token audience | (defaults to client ID) |
| `AZURE_AD_ALLOWED_CLIENT_IDS` | Comma-separated allowed apps | `app1,app2` |

### Server Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BACKEND_PORT` | Server port | `8000` |
| `LOG_FORMAT` | Log format (`text` or `json`) | `text` |
| `MAX_REQUEST_SIZE` | Max request body (bytes) | `1048576` (1MB) |

### Observability (Optional)

| Variable | Description | Example |
|----------|-------------|---------|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights connection | `InstrumentationKey=...` |

---

## Frontend Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `BACKEND_URL`* | Backend API URL | `http://localhost:8000` |

### Optional

| Variable | Description | Example |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Client-side API URL (if different) | `https://api.example.com` |

---

## Environment Files

| File | Purpose | Committed to Git |
|------|---------|------------------|
| `apps/backend/.env.example` | Template with all variables | Yes |
| `apps/backend/.env` | Actual secrets | **NO** |
| `apps/frontend/.env.local` | Frontend config | **NO** |

---

## Production vs Development

### Development Defaults

```bash
USE_ON_YOUR_DATA=true
USE_COHERE_RERANK=true
FAIL_ON_MISSING_CONFIG=false
REQUIRE_AAD_AUTH=false
CORS_ORIGINS=http://localhost:3000
LOG_FORMAT=text
```

### Production Requirements

```bash
# Must be set in production
FAIL_ON_MISSING_CONFIG=true
ADMIN_API_KEY=<secure-random-key>
CORS_ORIGINS=https://your-frontend-domain.com

# Recommended in production
REQUIRE_AAD_AUTH=true
LOG_FORMAT=json
```

---

## Azure Container Apps Environment

When deploying to Azure Container Apps, set environment variables via:

```bash
az containerapp update \
  --name rush-policy-backend \
  --resource-group RU-A-NonProd-AI-Innovation-RG \
  --set-env-vars \
    "SEARCH_ENDPOINT=https://..." \
    "SEARCH_API_KEY=secretref:search-api-key" \
    "AOAI_ENDPOINT=https://..." \
    "AOAI_API_KEY=secretref:aoai-api-key"
```

For secrets, use `secretref:` prefix and configure secrets in Container Apps.

See [DEPLOYMENT.md](../DEPLOYMENT.md) for complete deployment instructions.

---

## Validation

The backend validates configuration at startup:

1. **Required variables** - Fails if missing when `FAIL_ON_MISSING_CONFIG=true`
2. **Service connectivity** - Tests Azure Search and OpenAI connections
3. **Feature dependencies** - Warns if `USE_COHERE_RERANK=true` but credentials missing

Check `/health` endpoint for configuration status:

```bash
curl http://localhost:8000/health | jq '.on_your_data, .search_index'
```
