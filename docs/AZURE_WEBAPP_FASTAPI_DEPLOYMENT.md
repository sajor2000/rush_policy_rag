# Azure Web App Service - FastAPI Deployment Guide

This document provides detailed instructions for deploying the RUSH Policy RAG FastAPI backend to Azure Web App Service. It addresses key differences from Flask deployments and common issues encountered.

---

## Table of Contents

1. [Key Differences: Flask vs FastAPI](#key-differences-flask-vs-fastapi)
2. [Prerequisites](#prerequisites)
3. [Deployment Option 1: Azure Web App Service (Python)](#deployment-option-1-azure-web-app-service-python)
4. [Deployment Option 2: Azure Web App for Containers (Docker)](#deployment-option-2-azure-web-app-for-containers-docker)
5. [Deployment Option 3: Azure Container Apps (Recommended)](#deployment-option-3-azure-container-apps-recommended)
6. [Using .env Files with Azure Deployments](#using-env-files-with-azure-deployments)
7. [Common Issues & Solutions](#common-issues--solutions)
8. [Environment Variables Reference](#environment-variables-reference)
9. [Health Check Endpoints](#health-check-endpoints)
10. [Troubleshooting Commands](#troubleshooting-commands)

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

**Using the automated script (Recommended):**

```bash
# Create optimized ZIP deployment package with validation
./scripts/deploy/create-zip-deploy.sh

# This will create apps/backend/deploy.zip with proper exclusions
# Then deploy:
az webapp deployment source config-zip \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --src apps/backend/deploy.zip
```

**Manual ZIP creation:**

```bash
# From apps/backend directory
cd apps/backend

# Create deployment package (excludes unnecessary files)
zip -r deploy.zip . \
  -x "*.pyc" \
  -x "__pycache__/*" \
  -x ".env" \
  -x "venv/*" \
  -x ".venv/*" \
  -x "tests/*" \
  -x "data/test_pdfs/*" \
  -x "Dockerfile" \
  -x "evaluation/*"

# Deploy
az webapp deployment source config-zip \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --src deploy.zip
```

**Before deploying, analyze any existing issues:**

```bash
# Analyze current configuration and identify problems
./scripts/deploy/analyze-zip-deploy-issues.sh <resource-group> <app-name> [zip-file]
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

**Option A: Using Automated Script (Recommended)**

The deployment script automatically loads environment variables from your `.env` file:

```bash
# Deploy with automatic .env file loading
./scripts/deploy/webapp_containers_deploy.sh

# Or with custom configuration
RESOURCE_GROUP="rg-rush-policy-prod" \
APP_NAME="rush-policy-backend" \
ACR_NAME="rushpolicyacr" \
./scripts/deploy/webapp_containers_deploy.sh
```

**Option B: Manual Configuration**

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

**Option A: Using Automated Script (Recommended)**

The deployment script automatically loads environment variables from your `.env` file:

```bash
# Deploy with automatic .env file loading
./scripts/deploy/aca_deploy.sh

# Or with custom configuration
RESOURCE_GROUP="rg-rush-policy-prod" \
CONTAINER_APP_NAME="rush-policy-backend" \
ACR_NAME="rushpolicyacr" \
./scripts/deploy/aca_deploy.sh
```

The script will:
- Automatically parse your `.env` file
- Set non-sensitive variables as environment variables
- Set sensitive variables (API keys, connection strings) as secrets
- Link secrets to environment variable references

**Option B: Manual Configuration**

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

### Step 3: Configure Secrets for Sensitive Values (Manual Only)

If not using the automated script, configure secrets manually:

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

## Using .env Files with Azure Deployments

### Problem: Docker `--env-file` Doesn't Work in Azure

When deploying Docker containers to Azure, you cannot use Docker's `--env-file` flag like you can locally:

```bash
# This works locally:
docker run --env-file .env -p 8000:8000 rush-policy-api:latest

# But Azure doesn't support --env-file directly
```

Azure services require environment variables to be set through Azure-specific configuration mechanisms.

### Solution: Automated .env File Loading

The deployment scripts now automatically convert your `.env` file to Azure-compatible format:

1. **For Azure Container Apps**: Uses `scripts/deploy/aca_deploy.sh`
   - Automatically loads `.env` file from project root
   - Separates sensitive vs non-sensitive variables
   - Sets secrets for sensitive values (API keys, connection strings)
   - Sets environment variables for non-sensitive values

2. **For Azure Web App for Containers**: Uses `scripts/deploy/webapp_containers_deploy.sh`
   - Automatically loads `.env` file from project root
   - Converts all variables to Azure App Settings
   - Azure handles encryption for sensitive values

### Usage

**Container Apps Deployment:**

```bash
# Basic usage (uses .env from project root)
./scripts/deploy/aca_deploy.sh

# With custom .env file location
ENV_FILE="/path/to/.env.production" ./scripts/deploy/aca_deploy.sh

# Disable auto-loading (use ENV_VARS environment variable instead)
AUTO_LOAD_ENV=false ENV_VARS="KEY1=value1 KEY2=value2" ./scripts/deploy/aca_deploy.sh
```

**Web App for Containers Deployment:**

```bash
# Basic usage (uses .env from project root)
./scripts/deploy/webapp_containers_deploy.sh

# With custom configuration
RESOURCE_GROUP="rg-rush-policy-prod" \
APP_NAME="rush-policy-backend" \
ACR_NAME="rushpolicyacr" \
./scripts/deploy/webapp_containers_deploy.sh
```

### Manual Environment Variable Updates

To update environment variables for an existing deployment without redeploying:

```bash
# For Container Apps
./scripts/deploy/set-env-vars.sh container-apps <resource-group> <app-name> [env-file]

# For Web App for Containers
./scripts/deploy/set-env-vars.sh webapp-containers <resource-group> <app-name> [env-file]

# Examples:
./scripts/deploy/set-env-vars.sh container-apps rush-rg rush-policy-api
./scripts/deploy/set-env-vars.sh webapp-containers rush-rg rush-policy-backend .env.production
```

### How It Works

The `convert-env-to-azure.sh` script:

1. Parses your `.env` file (handles comments, empty lines, quoted values)
2. Identifies sensitive variables (API keys, connection strings, etc.)
3. Generates Azure CLI-compatible format:
   - **Container Apps**: Separates into `ENV_VARS` (non-sensitive) and `SECRET_VARS` (sensitive)
   - **Web App**: Converts all to `APP_SETTINGS` format

### Sensitive Variables

By default, these variables are treated as sensitive and stored as secrets (Container Apps) or encrypted settings (Web App):

- `SEARCH_API_KEY`
- `AOAI_API_KEY` / `AOAI_API`
- `COHERE_RERANK_API_KEY`
- `ADMIN_API_KEY`
- `STORAGE_CONNECTION_STRING`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`
- `AZURE_AD_TENANT_ID`
- `AZURE_AD_CLIENT_ID`
- `AZURE_AD_CLIENT_SECRET`

You can customize this list by providing a file with variable names (one per line) to the converter script.

### Troubleshooting

**Issue: Environment variables not loading**

- Verify `.env` file exists at project root (or specify path with `ENV_FILE`)
- Check file permissions (script must be able to read the file)
- Ensure `AUTO_LOAD_ENV=true` (default) is set
- Check script output for parsing errors

**Issue: Secrets not being set**

- For Container Apps, secrets must be set before environment variables that reference them
- Verify you have permissions to set secrets in the Container App
- Check that secret names match the environment variable names

**Issue: Variables with special characters**

- The script handles quoted values and escaped characters
- If issues persist, check the `.env` file format (should be `KEY=VALUE` or `KEY="VALUE"`)

### References

This implementation follows Azure best practices for environment variable management:

- **Azure Container Apps**: [Environment Variables Documentation](https://learn.microsoft.com/azure/container-apps/environment-variables)
- **Azure Web App for Containers**: [Configure Custom Containers](https://learn.microsoft.com/azure/app-service/configure-custom-container)
- **Azure CLI Reference**:
  - [az containerapp update](https://learn.microsoft.com/cli/azure/containerapp#az-containerapp-update) - `--env-vars` parameter
  - [az webapp config appsettings set](https://learn.microsoft.com/cli/azure/webapp/config/appsettings#az-webapp-config-appsettings-set) - `--settings` parameter

**Key Best Practices Implemented:**

1. ✅ **Secrets Management**: Sensitive variables (API keys, connection strings) are stored as secrets in Container Apps
2. ✅ **Secret References**: Container Apps use `secretref:` to reference secrets securely
3. ✅ **Encryption**: Web App for Containers automatically encrypts sensitive app settings
4. ✅ **Separation of Concerns**: Non-sensitive config vs sensitive secrets are handled differently
5. ✅ **Automation**: Scripts automate the conversion from `.env` to Azure-compatible format

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

### Issue 7: Missing Environment Variables in Azure

**Symptom**: Application fails because environment variables are missing or not set correctly in Azure, even though `docker run --env-file .env` works locally.

**Cause**: Azure services don't support Docker's `--env-file` flag. Environment variables must be set through Azure-specific configuration.

**Solution**:
1. Use the automated deployment scripts that convert `.env` files automatically:
   ```bash
   # For Container Apps
   ./scripts/deploy/aca_deploy.sh
   
   # For Web App for Containers
   ./scripts/deploy/webapp_containers_deploy.sh
   ```

2. Or manually update environment variables using the helper script:
   ```bash
   ./scripts/deploy/set-env-vars.sh container-apps <resource-group> <app-name>
   ```

3. Verify variables are set:
   ```bash
   # Container Apps
   az containerapp show --name <app-name> --resource-group <rg> \
     --query 'properties.template.containers[0].env'
   
   # Web App
   az webapp config appsettings list --name <app-name> --resource-group <rg>
   ```

### Issue 8: ZIP Deploy Failure - "Package deployment using ZIP Deploy failed"

**Symptom**: ZIP deployment fails with error "Failed to deploy web package to App Service" or "Package deployment using ZIP Deploy failed".

**Common Causes**:
1. **Python version mismatch**: Azure Web App Service may not support very new Python versions (e.g., 3.13.8)
2. **Missing startup command**: FastAPI requires explicit startup command configuration
3. **Incorrect file structure**: `requirements.txt` or `main.py` not in correct location
4. **Missing app settings**: Required settings like `WEBSITES_PORT` not configured

**Solution**:

**Option A: Use the fix script (Recommended)**
```bash
# Fix common ZIP deploy configuration issues
./scripts/deploy/fix-zip-deploy.sh <resource-group> <app-name>

# Then redeploy
cd apps/backend
zip -r deploy.zip . -x "*.pyc" -x "__pycache__/*" -x ".env" -x "venv/*" -x ".venv/*"
az webapp deployment source config-zip \
  --name <app-name> \
  --resource-group <resource-group> \
  --src deploy.zip
```

**Option B: Manual Fix**
```bash
# 1. Set Python version to 3.11 (recommended, well-supported)
az webapp config set \
  --name <app-name> \
  --resource-group <resource-group> \
  --linux-fx-version "PYTHON|3.11"

# 2. Configure startup command
az webapp config set \
  --name <app-name> \
  --resource-group <resource-group> \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000"

# 3. Set required app settings
az webapp config appsettings set \
  --name <app-name> \
  --resource-group <resource-group> \
  --settings \
    WEBSITES_PORT=8000 \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true

# 4. Verify requirements.txt is in ZIP root
# Ensure your ZIP file structure is:
#   deploy.zip
#   ├── main.py
#   ├── requirements.txt
#   ├── app/
#   └── ...
```

**Option C: Switch to Container Deployment (Recommended for FastAPI)**

ZIP deploy can be unreliable for FastAPI. Consider switching to:
- **Azure Web App for Containers**: Uses Docker, more reliable
  ```bash
  ./scripts/deploy/webapp_containers_deploy.sh
  ```
- **Azure Container Apps**: Best option for containerized FastAPI apps
  ```bash
  ./scripts/deploy/aca_deploy.sh
  ```

**Troubleshooting ZIP Deploy**:
```bash
# Check current Python version
az webapp config show --name <app-name> --resource-group <rg> --query "linuxFxVersion"

# View deployment logs
az webapp log tail --name <app-name> --resource-group <rg>

# Download detailed logs
az webapp log download --name <app-name> --resource-group <rg> --log-file logs.zip

# Check if app is running
az webapp show --name <app-name> --resource-group <rg> --query "state"
```

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
