# Deployment Guide

> **Quick Reference for RUSH Tech Team**
>
> **Prerequisites**: Azure CLI installed, logged in (`az login`), access to Rush Azure tenant
>
> **One-Time Setup**: Create the AI Agent first (see Pre-Deployment section)
>
> **Deploy Backend**: `az acr build` → `az containerapp create`
>
> **Deploy Frontend**: `az acr build` → `az containerapp create`
>
> **Verify**: `curl https://<backend>/health` should return `{"status": "healthy"}`

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│             PRODUCTION ARCHITECTURE (v3.0 - Full Azure)              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   User Browser                                                       │
│        │                                                             │
│        ▼                                                             │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                  Azure Container Apps                        │   │
│   │  ┌─────────────────┐         ┌─────────────────────────┐   │   │
│   │  │ rush-policy-    │  HTTP   │ rush-policy-            │   │   │
│   │  │ frontend        │ ──────► │ backend                 │   │   │
│   │  │ (Next.js)       │         │ (FastAPI)               │   │   │
│   │  └─────────────────┘         └───────────┬─────────────┘   │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                              │                       │
│                                              ▼                       │
│                           ┌────────────────────────┐                │
│                           │   Azure AI Foundry     │                │
│                           │   Persistent Agent     │                │
│                           │   (rush-policy-agent)  │                │
│                           └───────────┬────────────┘                │
│                                       │                              │
│                    ┌──────────────────┼──────────────────┐          │
│                    │                  │                  │          │
│                    ▼                  ▼                  ▼          │
│             ┌──────────┐       ┌──────────┐       ┌──────────┐     │
│             │  Azure   │       │  Azure   │       │  Azure   │     │
│             │   AI     │       │  OpenAI  │       │  Blob    │     │
│             │  Search  │       │ (GPT-4.1)│       │ Storage  │     │
│             └──────────┘       └──────────┘       └──────────┘     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

- **Frontend**: Next.js (App Router) → Azure Container Apps
- **Backend**: FastAPI → Azure Container Apps
- **Agent**: Azure AI Foundry Persistent Agent (VECTOR_SEMANTIC_HYBRID + top_k=50)
- **Search**: Azure AI Search (Hybrid + Semantic Ranking + RRF Fusion)
- **LLM**: Azure OpenAI (GPT-4.1)
- **Storage**: Azure Blob Storage (Policy PDFs)

---

## Option 1: Container Deployment (Recommended for Production)

Best for: Production environments, predictable scaling, isolation

### Backend - Azure Container Registry + Container Apps

#### Step 1: Build and Push Container Image

```bash
# Login to Azure
az login

# Create Container Registry (if needed)
az acr create \
  --resource-group <RG_NAME> \
  --name <ACR_NAME> \
  --sku Basic

# Build and push image
cd apps/backend
az acr build --registry <ACR_NAME> --image policytech-backend:latest .
```

#### Step 2: Deploy to Azure Container Apps

```bash
# Create Container Apps Environment (if needed)
az containerapp env create \
  --name policytech-env \
  --resource-group <RG_NAME> \
  --location eastus

# Create secrets for sensitive values
az containerapp secret set \
  --name policytech-backend \
  --resource-group <RG_NAME> \
  --secrets \
    search-api-key=<SEARCH_API_KEY> \
    aoai-api-key=<AOAI_API_KEY> \
    storage-conn=<STORAGE_CONNECTION_STRING> \
    admin-key=<ADMIN_API_KEY>

# Deploy container
az containerapp create \
  --name policytech-backend \
  --resource-group <RG_NAME> \
  --environment policytech-env \
  --image <ACR_NAME>.azurecr.io/policytech-backend:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --env-vars \
    SEARCH_ENDPOINT=https://<search-service>.search.windows.net \
    SEARCH_API_KEY=secretref:search-api-key \
    SEARCH_INDEX_NAME=rush-policies \
    AOAI_ENDPOINT=https://<aoai-service>.openai.azure.com/ \
    AOAI_API=secretref:aoai-api-key \
    AOAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large \
    AOAI_CHAT_DEPLOYMENT=gpt-4.1 \
    STORAGE_CONNECTION_STRING=secretref:storage-conn \
    CONTAINER_NAME=policies-active \
    ADMIN_API_KEY=secretref:admin-key \
    BACKEND_PORT=8000
```

#### Alternative: Azure App Service (Container)

```bash
# Create App Service Plan
az appservice plan create \
  --name policytech-plan \
  --resource-group <RG_NAME> \
  --sku B1 \
  --is-linux

# Create Web App with container
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-backend \
  --deployment-container-image-name <ACR_NAME>.azurecr.io/policytech-backend:latest

# Configure ACR credentials
az webapp config container set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --container-registry-url https://<ACR_NAME>.azurecr.io \
  --container-registry-user <ACR_USERNAME> \
  --container-registry-password <ACR_PASSWORD>
```

### Frontend - Azure Container Apps

#### Step 1: Build and Push Frontend Container Image

```bash
# Build and push frontend image
cd apps/frontend
az acr build --registry <ACR_NAME> --image policytech-frontend:latest .
```

#### Step 2: Deploy Frontend to Azure Container Apps

Using Bicep (recommended):

```bash
# Deploy using Bicep template
az deployment group create \
  --resource-group <RG_NAME> \
  --template-file infrastructure/azure-container-app-frontend.bicep \
  --parameters \
    environment=production \
    containerImage=<ACR_NAME>.azurecr.io/policytech-frontend:latest \
    registryPassword=<ACR_PASSWORD> \
    backendUrl=https://rush-policy-backend-production.<REGION>.azurecontainerapps.io
```

Or using Azure CLI:

```bash
# Deploy frontend container
az containerapp create \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --environment rush-policy-env-production \
  --image <ACR_NAME>.azurecr.io/policytech-frontend:latest \
  --target-port 3000 \
  --ingress external \
  --min-replicas 2 \
  --max-replicas 10 \
  --env-vars \
    BACKEND_URL=https://rush-policy-backend-production.<REGION>.azurecontainerapps.io \
    NODE_ENV=production
```

#### Step 3: Configure Custom Domain (Optional)

```bash
# Add custom domain (e.g., policy.rush.edu)
az containerapp hostname add \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --hostname policy.rush.edu

# Bind certificate
az containerapp hostname bind \
  --name rush-policy-frontend \
  --resource-group <RG_NAME> \
  --hostname policy.rush.edu \
  --certificate <CERTIFICATE_NAME>
```

---

## Option 2: Source Deployment (Simpler Setup)

Best for: Development, staging, quick iterations

### Backend - Azure App Service (Python Runtime)

#### Step 1: Create App Service

```bash
# Create App Service Plan (if needed)
az appservice plan create \
  --name policytech-plan \
  --resource-group <RG_NAME> \
  --sku B1 \
  --is-linux

# Create Web App with Python runtime
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-backend \
  --runtime "PYTHON:3.11"
```

#### Step 2: Configure Startup

```bash
# Set startup command
az webapp config set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --startup-file "cd apps/backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 8000"
```

#### Step 3: Deploy from GitHub

```bash
# Configure deployment source
az webapp deployment source config \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --repo-url https://github.com/<org>/<repo> \
  --branch main \
  --manual-integration
```

Or use GitHub Actions:

```yaml
# .github/workflows/deploy-backend.yml
name: Deploy Backend to Azure

on:
  push:
    branches: [main]
    paths:
      - 'apps/backend/**'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: azure/webapps-deploy@v2
        with:
          app-name: policytech-backend
          publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
          package: apps/backend
```

#### Step 4: Set Environment Variables

```bash
az webapp config appsettings set \
  --resource-group <RG_NAME> \
  --name policytech-backend \
  --settings \
    SEARCH_ENDPOINT="https://<search-service>.search.windows.net" \
    SEARCH_API_KEY="<value>" \
    SEARCH_INDEX_NAME="rush-policies" \
    AOAI_ENDPOINT="https://<aoai-service>.openai.azure.com/" \
    AOAI_API="<value>" \
    AOAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1" \
    STORAGE_CONNECTION_STRING="<value>" \
    CONTAINER_NAME="policies-active" \
    ADMIN_API_KEY="<value>" \
    BACKEND_PORT="8000"
```

### Frontend - Azure App Service (Node.js Runtime)

```bash
# Create App Service for frontend
az webapp create \
  --resource-group <RG_NAME> \
  --plan policytech-plan \
  --name policytech-frontend \
  --runtime "NODE:18-lts"

# Configure startup
az webapp config set \
  --resource-group <RG_NAME> \
  --name policytech-frontend \
  --startup-file "cd apps/frontend && npm install && npm run build && npm start"

# Set environment variables
az webapp config appsettings set \
  --resource-group <RG_NAME> \
  --name policytech-frontend \
  --settings \
    BACKEND_URL="https://policytech-backend.azurecontainerapps.io" \
    NODE_ENV="production"
```

---

## Pre-Deployment: Create Persistent Agent

Before deploying, create the Azure AI Foundry agent that will be reused by all instances:

```bash
# 1. Login to Azure
az login

# 2. Set environment variables in .env
AZURE_AI_PROJECT_ENDPOINT=https://<ai-services>.services.ai.azure.com/api/projects/<project>

# 3. Create the persistent agent
source .venv/bin/activate
python scripts/create_foundry_agent.py

# 4. Copy the agent ID to .env
FOUNDRY_AGENT_ID=asst_xxxxxxxxxxxxx
```

The agent is created once and reused across all deployments. Use `--update` to modify the agent configuration.

---

## Environment Variables Reference

### Backend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_AI_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint | `https://<ai>.services.ai.azure.com/api/projects/<proj>` |
| `FOUNDRY_AGENT_ID` | Persistent agent ID (from create script) | `asst_WQSFmyXMHpJedZM0Rwo43zk1` |
| `SEARCH_ENDPOINT` | Azure AI Search endpoint URL | `https://mysearch.search.windows.net` |
| `SEARCH_API_KEY` | Search admin API key | `abc123...` |
| `STORAGE_CONNECTION_STRING` | Azure Storage connection string | `DefaultEndpointsProtocol=https;...` |
| `CONTAINER_NAME` | Blob container name | `policies-active` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights (optional) | `InstrumentationKey=...` |
| `BACKEND_PORT` | Server port (optional) | `8000` |
| `CORS_ORIGINS` | Allowed CORS origins (optional) | `https://rush-policy-frontend.azurecontainerapps.io` |

### Frontend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `BACKEND_URL` | Full URL to backend API | `https://policytech-backend.azurecontainerapps.io` |
| `NEXT_PUBLIC_APP_NAME` | App display name (optional) | `PolicyTech` |

### Legacy Variables (Fallback if Foundry not configured)

| Variable | Description |
|----------|-------------|
| `AOAI_ENDPOINT` | Direct Azure OpenAI endpoint |
| `AOAI_API` | Azure OpenAI API key |
| `AOAI_CHAT_DEPLOYMENT` | Chat model deployment name |
| `AOAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment name |

---

## CORS Configuration

The backend CORS policy is configured in the Bicep template (`infrastructure/azure-container-app.bicep`):

```bicep
corsPolicy: {
  allowedOrigins: [
    'https://rush-policy-frontend.${containerAppEnvironment.properties.defaultDomain}'
    'https://${customFrontendDomain}'  // Optional custom domain
  ]
  allowedMethods: ['GET', 'POST', 'OPTIONS']
  allowedHeaders: ['Content-Type', 'Authorization', 'X-Admin-Key']
  maxAge: 86400
}
```

For local development, CORS is configured in `apps/backend/app/core/config.py`:

```python
ALLOWED_ORIGINS: list = [
    "http://localhost:3000",
    "http://localhost:5000",
]
```

---

## Post-Deployment Checklist

### Backend Verification

- [ ] Health check passes: `curl https://<backend>/health`
- [ ] Search index stats visible in health response
- [ ] Test search endpoint: `POST /api/search` with sample query
- [ ] Test chat endpoint: `POST /api/chat` with sample question
- [ ] Admin endpoints protected (returns 403 without X-Admin-Key)
- [ ] PDF SAS URL generation works: `GET /api/pdf/<filename>.pdf`

### Frontend Verification

- [ ] App loads without errors
- [ ] Chat interface displays correctly
- [ ] Messages send and receive responses
- [ ] Policy citations display with links
- [ ] PDF viewer opens source documents
- [ ] Mobile responsive layout works

### Integration Verification

- [ ] CORS headers present in backend responses
- [ ] No mixed content warnings (HTTPS everywhere)
- [ ] Response times acceptable (<5s for queries)
- [ ] Error messages display gracefully

---

## Troubleshooting

### Backend Issues

| Issue | Solution |
|-------|----------|
| Container fails to start | Check logs: `az containerapp logs show --name policytech-backend --resource-group <RG>` |
| 503 Service Unavailable | Verify container health, check memory/CPU limits |
| Search returns empty | Verify index exists and has documents, check SEARCH_API_KEY |
| Chat returns 500 | Check AOAI credentials and deployment names |

### Frontend Issues

| Issue | Solution |
|-------|----------|
| Container fails to start | Check logs: `az containerapp logs show --name rush-policy-frontend --resource-group <RG>` |
| CORS errors | Verify frontend domain in backend Bicep `corsPolicy.allowedOrigins` |
| API calls fail | Verify BACKEND_URL environment variable, check network connectivity |
| Build fails | Verify Dockerfile, check Node.js version in container |

### Azure Issues

| Issue | Solution |
|-------|----------|
| ACR image pull fails | Check registry credentials, enable admin user on ACR |
| Secrets not resolving | Verify secret names match secretref values |
| Slow cold starts | Increase min replicas, use premium tier |

---

## Cost Optimization

### Development/Staging
- Use Azure App Service B1 tier (~$13/month)
- Single replica on Container Apps for both frontend and backend
- Consumption plan for low-traffic periods

### Production
- Azure Container Apps with autoscaling (2-10 replicas each)
- Azure AI Search Basic tier
- Azure OpenAI pay-per-token
- Custom domain with Azure-managed certificate

---

## Security Recommendations

1. **Secrets Management**
   - Use Azure Key Vault for production secrets
   - Never commit secrets to git
   - Rotate API keys periodically
   - Use Container Apps secrets with `secretRef`

2. **Network Security**
   - Enable HTTPS only (automatic on Azure Container Apps)
   - Configure Web Application Firewall (WAF) for production
   - Use Private Endpoints for Azure services in enterprise
   - Consider Azure Front Door for global load balancing

3. **Authentication**
   - Protect admin endpoints with strong API key
   - Consider Azure AD integration for internal users (Rush employees)
   - Implement rate limiting for public endpoints
   - Use Managed Identity for Azure service authentication

4. **Compliance (RUSH Healthcare)**
   - Ensure HIPAA compliance for any PHI
   - Enable audit logging via Application Insights
   - Configure log retention policies per RUSH requirements
