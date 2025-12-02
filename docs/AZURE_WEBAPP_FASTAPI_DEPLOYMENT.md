# Azure Web App Service - FastAPI Deployment Guide

This document provides detailed instructions for deploying the RUSH Policy RAG FastAPI backend to Azure Web App Service. It addresses key differences from Flask deployments and common issues encountered.

---

## Table of Contents

1. [Key Differences: Flask vs FastAPI](#key-differences-flask-vs-fastapi)
2. [Prerequisites](#prerequisites)
3. [Deployment Option 1: Azure Web App Service (Python)](#deployment-option-1-azure-web-app-service-python)
4. [Deployment Option 2: Azure Web App for Containers (Docker)](#deployment-option-2-azure-web-app-for-containers-docker)
5. [Deployment Option 3: Azure Container Apps (Recommended)](#deployment-option-3-azure-container-apps-recommended)
6. [Common Issues & Solutions](#common-issues--solutions)
7. [Environment Variables Reference](#environment-variables-reference)
8. [Health Check Endpoints](#health-check-endpoints)
9. [Troubleshooting Commands](#troubleshooting-commands)

---

## Key Differences: Flask vs FastAPI

| Aspect | Flask | FastAPI |
|--------|-------|---------|
| **Protocol** | WSGI (synchronous) | ASGI (asynchronous) |
| **Server** | Gunicorn directly | Uvicorn or Gunicorn with UvicornWorker |
| **Entry Point** | `application.py` or `app.py` | Same, but needs ASGI-compatible startup |
| **Startup Command** | `gunicorn app:app` | `gunicorn -k uvicorn.workers.UvicornWorker main:app` |
| **Default Port** | 5000 | 8000 |

### Why This Matters

Azure Web App Service is optimized for WSGI (Flask/Django). FastAPI uses ASGI, which requires:
1. **Explicit startup command** - Azure won't auto-detect the correct way to run FastAPI
2. **Uvicorn worker class** - Must specify `uvicorn.workers.UvicornWorker` when using Gunicorn
3. **Correct dependencies** - `uvicorn[standard]` must be in requirements.txt

---

## Prerequisites

### Required Azure Resources

| Resource | Purpose | Required |
|----------|---------|----------|
| Azure AI Search | Vector store for policy documents | Yes |
| Azure OpenAI | GPT-4.1 + text-embedding-3-large | Yes |
| Azure Blob Storage | PDF document storage | Yes |
| Azure AI Foundry | Cohere Rerank 3.5 deployment | Yes |
| Azure Web App / Container Apps | Host the application | Yes |

### Required Dependencies in `requirements.txt`

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
gunicorn>=21.2.0
```

Verify these are present in `apps/backend/requirements.txt`.

---

## Deployment Option 1: Azure Web App Service (Python)

### Step 1: Create the Web App

```bash
# Variables
RESOURCE_GROUP="rg-rush-policy-prod"
LOCATION="eastus2"
APP_NAME="rush-policy-backend"
APP_SERVICE_PLAN="asp-rush-policy-prod"

# Create App Service Plan (Linux, B2 or higher recommended)
az appservice plan create \
  --name $APP_SERVICE_PLAN \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --is-linux \
  --sku B2

# Create Web App with Python 3.11
az webapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --plan $APP_SERVICE_PLAN \
  --runtime "PYTHON:3.11"
```

### Step 2: Configure Startup Command (CRITICAL)

This is the most common issue. Azure needs to know how to start FastAPI.

```bash
# Option A: Using Gunicorn with Uvicorn workers (RECOMMENDED for production)
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000"

# Option B: Using Uvicorn directly (simpler, but single worker)
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "uvicorn main:app --host 0.0.0.0 --port 8000"
```

### Step 3: Set Environment Variables

```bash
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    SEARCH_ENDPOINT="https://policychataisearch.search.windows.net" \
    SEARCH_API_KEY="<your-search-api-key>" \
    AOAI_ENDPOINT="https://rua-nonprod-ai-innovation.openai.azure.com/" \
    AOAI_API_KEY="<your-aoai-api-key>" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    STORAGE_CONNECTION_STRING="<your-storage-connection-string>" \
    CONTAINER_NAME="policies-active" \
    USE_ON_YOUR_DATA="true" \
    USE_COHERE_RERANK="true" \
    COHERE_RERANK_ENDPOINT="https://Cohere-rerank-v3-5-beomo.eastus2.models.ai.azure.com/v1/rerank" \
    COHERE_RERANK_API_KEY="<your-cohere-api-key>" \
    COHERE_RERANK_MODEL="cohere-rerank-v3-5" \
    COHERE_RERANK_TOP_N="10" \
    COHERE_RERANK_MIN_SCORE="0.15" \
    WEBSITES_PORT="8000"
```

**Important**: Set `WEBSITES_PORT=8000` to tell Azure which port the app listens on.

### Step 4: Deploy the Code

**Option A: ZIP Deploy (Recommended)**

```bash
# From apps/backend directory
cd apps/backend

# Create deployment package
zip -r deploy.zip . -x "*.pyc" -x "__pycache__/*" -x ".env" -x "venv/*" -x ".venv/*"

# Deploy
az webapp deployment source config-zip \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --src deploy.zip
```

**Option B: Git Deploy**

```bash
# Configure deployment source
az webapp deployment source config-local-git \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP

# Get the Git URL and push
git remote add azure <git-url-from-output>
git push azure main
```

### Step 5: Verify Deployment

```bash
# Check logs
az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP

# Test health endpoint
curl https://$APP_NAME.azurewebsites.net/health
```

---

## Deployment Option 2: Azure Web App for Containers (Docker)

This option uses the existing Dockerfile and is more reliable for FastAPI.

### Step 1: Build and Push Docker Image

```bash
# Variables
ACR_NAME="rushpolicyacr"
IMAGE_NAME="rush-policy-backend"
IMAGE_TAG="latest"

# Login to ACR
az acr login --name $ACR_NAME

# Build image
cd apps/backend
docker build -t $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG .

# Push to ACR
docker push $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG
```

### Step 2: Create Web App for Containers

```bash
# Create Web App with container
az webapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --plan $APP_SERVICE_PLAN \
  --deployment-container-image-name $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG

# Configure ACR credentials
az webapp config container set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --docker-custom-image-name $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG \
  --docker-registry-server-url https://$ACR_NAME.azurecr.io \
  --docker-registry-server-user $ACR_NAME \
  --docker-registry-server-password <acr-password>
```

### Step 3: Set Environment Variables

Same as Option 1, Step 3.

---

## Deployment Option 3: Azure Container Apps (Recommended)

Azure Container Apps is designed for containerized microservices and handles ASGI apps natively.

### Step 1: Create Container Apps Environment

```bash
# Variables
ENVIRONMENT_NAME="cae-rush-policy-prod"

# Create environment
az containerapp env create \
  --name $ENVIRONMENT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

### Step 2: Deploy Container App

```bash
az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $ENVIRONMENT_NAME \
  --image $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG \
  --target-port 8000 \
  --ingress external \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password <acr-password> \
  --cpu 1 \
  --memory 2Gi \
  --min-replicas 1 \
  --max-replicas 3 \
  --env-vars \
    SEARCH_ENDPOINT="https://policychataisearch.search.windows.net" \
    AOAI_ENDPOINT="https://rua-nonprod-ai-innovation.openai.azure.com/" \
    # ... (all other env vars as secrets)
```

### Step 3: Configure Secrets for Sensitive Values

```bash
az containerapp secret set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --secrets \
    search-api-key=<value> \
    aoai-api-key=<value> \
    storage-connection-string=<value> \
    cohere-api-key=<value>
```

---

## Common Issues & Solutions

### Issue 1: "Application Error" or Container Keeps Restarting

**Symptom**: Web App shows "Application Error" or container restarts repeatedly.

**Cause**: Missing or incorrect startup command.

**Solution**:
```bash
# Verify startup command is set
az webapp config show --name $APP_NAME --resource-group $RESOURCE_GROUP --query "linuxFxVersion"

# Set correct startup command
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000"
```

### Issue 2: "No module named 'uvicorn'"

**Symptom**: Logs show `ModuleNotFoundError: No module named 'uvicorn'`.

**Cause**: `uvicorn` not in requirements.txt or not installed.

**Solution**:
1. Verify `uvicorn[standard]>=0.27.0` is in `requirements.txt`
2. Redeploy the application
3. Check Oryx build logs for pip install errors

### Issue 3: Port Mismatch

**Symptom**: Application starts but returns 502 Bad Gateway.

**Cause**: Azure expects app on port 8000 by default, but app might be on different port.

**Solution**:
```bash
# Set the port Azure should expect
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings WEBSITES_PORT=8000

# Ensure startup command uses same port
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "uvicorn main:app --host 0.0.0.0 --port 8000"
```

### Issue 4: SSL/Certificate Errors (Corporate Proxy)

**Symptom**: `ssl.SSLCertVerificationError` when connecting to Azure services.

**Cause**: Corporate proxy (e.g., Netskope) intercepting SSL traffic.

**Solution**: The codebase includes `ssl_fix.py` which uses `truststore` library. This is already imported in `main.py`. Ensure `truststore` is in requirements.txt.

### Issue 5: Health Check Failing

**Symptom**: Container marked unhealthy, keeps restarting.

**Cause**: Health check endpoint returns error or times out.

**Solution**:
1. Verify `/health` endpoint works locally: `curl http://localhost:8000/health`
2. Check if Azure is hitting wrong path
3. Increase health check timeout:

```bash
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --generic-configurations '{"healthCheckPath": "/health"}'
```

### Issue 6: "Package not recognized"

**Symptom**: Azure shows "Package not recognized" or build fails.

**Cause**: Azure's Oryx build system doesn't detect Python app correctly.

**Solution**:
1. Ensure `requirements.txt` is in the root of the deployed folder
2. Add `startup.txt` file with startup command
3. Or switch to Docker deployment (more reliable)

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SEARCH_ENDPOINT` | Yes | Azure AI Search endpoint URL |
| `SEARCH_API_KEY` | Yes | Azure AI Search API key |
| `AOAI_ENDPOINT` | Yes | Azure OpenAI endpoint URL |
| `AOAI_API_KEY` | Yes | Azure OpenAI API key |
| `AOAI_CHAT_DEPLOYMENT` | Yes | GPT model deployment name (e.g., `gpt-4.1`) |
| `AOAI_EMBEDDING_DEPLOYMENT` | Yes | Embedding model name (e.g., `text-embedding-3-large`) |
| `STORAGE_CONNECTION_STRING` | Yes | Azure Blob Storage connection string |
| `CONTAINER_NAME` | Yes | Blob container name (default: `policies-active`) |
| `USE_ON_YOUR_DATA` | Yes | Enable Azure OpenAI "On Your Data" (`true`) |
| `USE_COHERE_RERANK` | Yes | Enable Cohere reranking (`true`) |
| `COHERE_RERANK_ENDPOINT` | Yes | Cohere Rerank endpoint URL |
| `COHERE_RERANK_API_KEY` | Yes | Cohere API key |
| `COHERE_RERANK_MODEL` | Yes | Model name (`cohere-rerank-v3-5`) |
| `COHERE_RERANK_TOP_N` | No | Docs to keep after rerank (default: `10`) |
| `COHERE_RERANK_MIN_SCORE` | No | Minimum relevance score (default: `0.15`) |
| `WEBSITES_PORT` | Yes* | Port for Azure Web App (set to `8000`) |
| `CORS_ORIGINS` | No | Allowed CORS origins (comma-separated) |
| `ADMIN_API_KEY` | Prod | API key for admin endpoints |

*Required for Azure Web App Service, not needed for Container Apps.

---

## Health Check Endpoints

The backend exposes these health check endpoints:

| Endpoint | Purpose | Expected Response |
|----------|---------|-------------------|
| `/health` | Full health check | `{"status": "healthy", ...}` |
| `/` | Root redirect | Redirects to `/docs` |
| `/docs` | Swagger UI | Interactive API documentation |
| `/redoc` | ReDoc UI | Alternative API documentation |

### Health Check Response Example

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
    "query_type": "vectorSemanticHybrid",
    "enabled": true
  },
  "blob_storage": {
    "configured": true,
    "container": "policies-active",
    "accessible": true
  },
  "version": "3.0.0"
}
```

---

## Troubleshooting Commands

### View Application Logs

```bash
# Stream logs in real-time
az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP

# Download logs
az webapp log download --name $APP_NAME --resource-group $RESOURCE_GROUP --log-file logs.zip
```

### SSH into Container

```bash
az webapp ssh --name $APP_NAME --resource-group $RESOURCE_GROUP
```

### Check Container Status (Container Apps)

```bash
az containerapp logs show \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --type console
```

### Restart Application

```bash
# Web App
az webapp restart --name $APP_NAME --resource-group $RESOURCE_GROUP

# Container App
az containerapp revision restart \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --revision <revision-name>
```

### Check Configuration

```bash
# View all app settings
az webapp config appsettings list --name $APP_NAME --resource-group $RESOURCE_GROUP

# View startup command
az webapp config show --name $APP_NAME --resource-group $RESOURCE_GROUP --query "appCommandLine"
```

---

## Quick Reference: Deployment Checklist

- [ ] `requirements.txt` includes `uvicorn[standard]` and `gunicorn`
- [ ] Startup command explicitly set for FastAPI/Uvicorn
- [ ] `WEBSITES_PORT=8000` configured (Web App Service only)
- [ ] All required environment variables set
- [ ] Sensitive values stored as secrets (not plain text)
- [ ] `/health` endpoint accessible and returning 200
- [ ] CORS configured for frontend domain
- [ ] SSL certificates valid (check corporate proxy issues)

---

## Contact & Support

- **PolicyTech URL**: https://rushumc.navexone.com/
- **Repository**: https://github.com/sajor2000/rush_policy_rag
- **Documentation**: See `DEPLOYMENT.md` for full infrastructure setup

---

*Last updated: December 2024*
