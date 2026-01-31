# Dev and Prod Plan (Rush Policy Assistant)

Source of truth: `docs/RushPolicyAssistant_Architecture.pdf`.

## Environments

### Non-Prod (Development/Test)

- **Subscription**: `RU-Azure-NonProd`
- **Resource group**: `RU-A-NonProd-AI-Innovation-RG`
- **Container Apps environment**: `rush-policy-env-production`
- **Frontend**: `https://rush-policy-frontend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`
- **Backend**: `https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io`
- **Development domain**: Azure Container Apps default hostname (per PDF: “az-container-app default”)

### Prod

- **Subscription**: `RU-Azure-Prod`
- **Resource group**: `RU-A-Prod-AI-Innovation-RG`
- **Container Apps environment**: `rush-policy-env-production`
- **Frontend (custom domain)**: `https://policychat.rush.edu`
- **Backend**: TBD (Azure Container Apps default hostname; redacted in PDF). Use:

```bash
az containerapp list -g RU-A-Prod-AI-Innovation-RG -o table
az containerapp show -g RU-A-Prod-AI-Innovation-RG -n <backend-app-name> \
  --query properties.configuration.ingress.fqdn -o tsv
```

## Shared Services (per PDF)

- **GitHub**: `https://github.com/sajor2000/rush_policy_rag`
- **PolicyTech**: `https://rushumc.navexone.com/content/`
- **Azure AI Foundry**: `rua-nonprod-ai-innovation`
- **Azure AI Search**: `policychataisearch`
- **Azure Blob Storage**: `policytechrush`
- **Container Registry**: `aiinnovation`
- **Models**:
  - LLM: **GPT-4** (PDF text) / **GPT-4.1** (PDF diagram)
  - Embeddings: **text-embedding-3-large**
  - Cohere Rerank model: **TBD** (unspecified in PDF)

## Dev → Prod Deployment Flow

1) Build and deploy to **Non-Prod** (RU-Azure-NonProd).  
2) Run smoke checks: `/health`, UI load, and a sample chat query.  
3) Promote the same image tag to **Prod** (RU-Azure-Prod).  
4) Verify custom domain (`policychat.rush.edu`) and CORS.  

Use `DEPLOYMENT.md` for detailed CLI steps; change subscription/resource group for Prod.

## Notes and Gaps

- The PDF lists LLM as **GPT-4**. Current code defaults to `gpt-4.1`. Confirm which deployment name is correct for Prod and update `.env`/Container App settings.
- Cohere rerank model is **TBD**. Update env vars once confirmed.
