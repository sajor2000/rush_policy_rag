# Deployment Quick Reference Guide

Quick reference for deploying RUSH Policy RAG to Azure. For detailed documentation, see [AZURE_WEBAPP_FASTAPI_DEPLOYMENT.md](./AZURE_WEBAPP_FASTAPI_DEPLOYMENT.md).

---

## The Problem: `.env` Files Don't Work in Azure

```bash
# This works locally:
docker run --env-file .env -p 8000:8000 rush-policy-api:latest

# But Azure doesn't support --env-file!
```

**Solution**: Our deployment scripts automatically convert `.env` to Azure format.

---

## Quick Deploy Commands

### Option 1: Azure Container Apps (Recommended)

```bash
# One command - auto-loads .env
./scripts/deploy/aca_deploy.sh
```

Configuration via environment variables (defaults are already set correctly):
```bash
# Uses defaults: RU-A-NonProd-AI-Innovation-RG, rush-policy-backend, aiinnovation
./scripts/deploy/aca_deploy.sh

# Or override if needed:
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG" \
CONTAINER_APP_NAME="rush-policy-backend" \
ACR_NAME="aiinnovation" \
./scripts/deploy/aca_deploy.sh
```

### Option 2: Azure Web App for Containers

```bash
# One command - auto-loads .env
./scripts/deploy/webapp_containers_deploy.sh
```

Configuration via environment variables:
```bash
RESOURCE_GROUP="RU-A-NonProd-AI-Innovation-RG" \
APP_NAME="rush-policy-backend" \
ACR_NAME="aiinnovation" \
./scripts/deploy/webapp_containers_deploy.sh
```

### Option 3: Update Existing Deployment's Env Vars Only

```bash
# Container Apps
./scripts/deploy/set-env-vars.sh container-apps <resource-group> <app-name>

# Web App for Containers
./scripts/deploy/set-env-vars.sh webapp-containers <resource-group> <app-name>

# With custom .env file
./scripts/deploy/set-env-vars.sh container-apps RU-A-NonProd-AI-Innovation-RG rush-policy-backend .env.production
```

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    LOCAL (.env file)                            │
│  SEARCH_API_KEY=abc123                                         │
│  AOAI_ENDPOINT=https://...                                     │
│  STORAGE_CONNECTION_STRING=DefaultEndpoints...                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              convert-env-to-azure.sh                            │
│  Parses .env → Separates sensitive vs non-sensitive            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌─────────────────────┐         ┌─────────────────────┐
│   Non-Sensitive     │         │     Sensitive       │
│   (ENV_VARS)        │         │     (SECRETS)       │
│                     │         │                     │
│  AOAI_ENDPOINT      │         │  SEARCH_API_KEY     │
│  CONTAINER_NAME     │         │  AOAI_API_KEY       │
│  USE_COHERE_RERANK  │         │  STORAGE_CONN_STR   │
│  CORS_ORIGINS       │         │  COHERE_API_KEY     │
└─────────┬───────────┘         └─────────┬───────────┘
          │                               │
          ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AZURE                                    │
│  Container Apps: --env-vars + --secrets + secretref:           │
│  Web App: --settings (Azure handles encryption)                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Sensitive Variables (Auto-detected)

These variables are automatically stored as Azure secrets:

| Variable | Description |
|----------|-------------|
| `SEARCH_API_KEY` | Azure AI Search API key |
| `AOAI_API_KEY` / `AOAI_API` | Azure OpenAI API key |
| `COHERE_RERANK_API_KEY` | Cohere Rerank API key |
| `ADMIN_API_KEY` | Admin endpoint API key |
| `STORAGE_CONNECTION_STRING` | Azure Blob Storage connection string |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights connection |
| `AZURE_AD_TENANT_ID` | Azure AD tenant |
| `AZURE_AD_CLIENT_ID` | Azure AD client ID |
| `AZURE_AD_CLIENT_SECRET` | Azure AD client secret |

---

## Available Scripts

| Script | Purpose |
|--------|---------|
| `aca_deploy.sh` | Deploy to Azure Container Apps |
| `webapp_containers_deploy.sh` | Deploy to Web App for Containers |
| `set-env-vars.sh` | Update env vars for existing deployment |
| `convert-env-to-azure.sh` | Convert .env to Azure CLI format |
| `build_push.sh` | Build and push Docker image to ACR |
| `fix-zip-deploy.sh` | Fix ZIP deployment issues |
| `analyze-zip-deploy-issues.sh` | Diagnose ZIP deployment problems |

---

## Troubleshooting

### Problem: "Application Error" after deployment

1. Check startup command is set:
   ```bash
   az webapp config show --name <app> --resource-group <rg> --query "appCommandLine"
   ```

2. View logs:
   ```bash
   # Container Apps
   az containerapp logs show --name <app> --resource-group <rg>

   # Web App
   az webapp log tail --name <app> --resource-group <rg>
   ```

### Problem: Environment variables not loading

1. Verify `.env` file exists at project root
2. Check `AUTO_LOAD_ENV=true` (default)
3. Manually verify:
   ```bash
   # Container Apps
   az containerapp show --name <app> --resource-group <rg> \
     --query 'properties.template.containers[0].env'

   # Web App
   az webapp config appsettings list --name <app> --resource-group <rg>
   ```

### Problem: ZIP deploy fails with Python 3.13

Switch to Docker deployment or fix Python version:
```bash
./scripts/deploy/fix-zip-deploy.sh <resource-group> <app-name>
```

---

## Health Check

After deployment, verify the application is healthy:

```bash
curl https://<app-name>.azurewebsites.net/health
```

Expected response:
```json
{
  "status": "healthy",
  "search_index": {"document_count": 16980},
  "on_your_data": {"enabled": true},
  "blob_storage": {"accessible": true}
}
```

---

## Summary

| Deployment Method | Command | Best For |
|-------------------|---------|----------|
| Container Apps | `./scripts/deploy/aca_deploy.sh` | Production (recommended) |
| Web App for Containers | `./scripts/deploy/webapp_containers_deploy.sh` | Simpler management |
| Update env vars only | `./scripts/deploy/set-env-vars.sh` | Config changes |

All scripts automatically load `.env` and handle sensitive variables as secrets.
