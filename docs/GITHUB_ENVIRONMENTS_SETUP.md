# GitHub Environments Setup Guide

> Step-by-step instructions for configuring GitHub Environments, secrets, and branch protection for the RUSH Policy RAG deployment pipeline.
>
> Last Updated: 2026-02-12

---

## Prerequisites

- GitHub repository admin access
- Azure service principal credentials for each environment
- Completed review of [CICD_PIPELINE.md](CICD_PIPELINE.md) for pipeline architecture

---

## Step 1: Create the `nonprod` Environment

1. Go to **Settings** > **Environments** > **New environment**
2. Name: `nonprod`
3. Click **Configure environment**

### Configuration

| Setting | Value |
|---------|-------|
| Required reviewers | None (auto-deploy after CI passes) |
| Wait timer | 0 minutes |
| Deployment branches | Selected branches: `main` only |

### Secrets

Add the following secrets to the `nonprod` environment:

| Secret | Description |
|--------|-------------|
| `AZURE_CREDENTIALS` | Service principal JSON for `RU-Azure-NonProd` subscription |
| `BACKEND_URL` | `https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io` |

#### Creating the Azure Service Principal

```bash
az ad sp create-for-rbac \
  --name "github-deploy-nonprod" \
  --role contributor \
  --scopes /subscriptions/e5282183-61c9-4c17-a58a-9442db9594d5/resourceGroups/RU-A-NonProd-AI-Innovation-RG \
  --sdk-auth
```

Copy the JSON output as the `AZURE_CREDENTIALS` secret value.

---

## Step 2: Configure Repo-Level Secrets

These secrets are used by CI jobs and build jobs (not environment-specific):

| Secret | Used By |
|--------|---------|
| `ACR_USERNAME` | Build jobs (Docker login to ACR) |
| `ACR_PASSWORD` | Build jobs (Docker login to ACR) |
| `AOAI_ENDPOINT` | CI tests, evaluation workflows |
| `AOAI_API_KEY` | CI tests, evaluation workflows |
| `SEARCH_ENDPOINT` | CI tests, evaluation workflows |
| `SEARCH_API_KEY` | CI tests, evaluation workflows |
| `COHERE_RERANK_ENDPOINT` | Evaluation workflows |
| `COHERE_RERANK_API_KEY` | Evaluation workflows |
| `STORAGE_CONNECTION_STRING` | Evaluation workflows |

**Note:** `ACR_USERNAME` and `ACR_PASSWORD` stay at repo level because the container registry is shared across all environments. Only `AZURE_CREDENTIALS` (the deployment service principal) is environment-scoped.

---

## Step 3: Set Up Branch Protection Rules

Go to **Settings** > **Branches** > **Add branch protection rule**

| Rule | Value |
|------|-------|
| Branch name pattern | `main` |
| Require a pull request before merging | Yes |
| Required approving reviews | 1 |
| Require status checks to pass before merging | Yes |
| Required status checks | `CI Gate` |
| Require branches to be up to date before merging | Yes |
| Include administrators | Yes (audit compliance) |

### Finding the `CI Gate` Status Check

The `CI Gate` status check will appear in the list after the first CI workflow run completes. If it doesn't appear:

1. Push a test commit to a branch and open a PR to `main`
2. Wait for CI to complete
3. Return to branch protection settings and search for `CI Gate`

---

## Step 4: Verify Configuration

### Test the Pipeline

1. Create a feature branch with a small change
2. Open a PR to `main`
3. Verify `CI Gate` appears as a required status check
4. Merge the PR
5. Verify `deploy.yml` triggers automatically
6. Check the `nonprod` environment shows a deployment in **Settings** > **Environments**

### Test CI Blocking

1. Create a branch with a deliberate lint error (e.g., missing trailing newline)
2. Open a PR to `main`
3. Verify `CI Gate` fails and the PR cannot be merged

---

## Future: Create `production` Environment

When production Azure resources are provisioned:

### 1. Create the Environment

1. Go to **Settings** > **Environments** > **New environment**
2. Name: `production`

### 2. Configure Settings

| Setting | Value |
|---------|-------|
| Required reviewers | 1+ named approver(s) |
| Wait timer | 5 minutes (optional cooldown) |
| Deployment branches | Selected branches: `main` only |

### 3. Add Secrets

| Secret | Description |
|--------|-------------|
| `AZURE_CREDENTIALS` | Service principal JSON for `RU-Azure-Prod` subscription |
| `BACKEND_URL` | Production backend URL (e.g., `https://policychat.rush.edu`) |

### 4. Enable in Pipeline

Uncomment the `deploy-production` job in `.github/workflows/deploy.yml`.

### 5. Test

Run a manual dispatch deployment first to verify the production environment works before relying on automatic triggers.

---

## Secret Scoping Summary

```
Repository secrets (shared):
├── ACR_USERNAME          ← Container registry login
├── ACR_PASSWORD          ← Container registry login
├── AOAI_ENDPOINT         ← CI tests
├── AOAI_API_KEY          ← CI tests
├── SEARCH_ENDPOINT       ← CI tests
├── SEARCH_API_KEY        ← CI tests
└── ...                   ← Other CI/eval secrets

Environment: nonprod
├── AZURE_CREDENTIALS     ← NonProd service principal
└── BACKEND_URL           ← NonProd backend URL

Environment: production (future)
├── AZURE_CREDENTIALS     ← Prod service principal
└── BACKEND_URL           ← Prod backend URL
```

---

## Related Documentation

- [CICD_PIPELINE.md](CICD_PIPELINE.md) — Pipeline architecture and deployment flow
- [DEPLOYMENT.md](DEPLOYMENT.md) — Manual Azure deployment procedures
- [SECURITY.md](SECURITY.md) — Security architecture including CI/CD security
