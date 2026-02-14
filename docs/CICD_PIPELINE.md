# CI/CD Pipeline

> Automated build, test, and deployment pipeline for the RUSH Policy RAG Agent.
>
> Last Updated: 2026-02-12

---

## Pipeline Architecture

```
Push/PR to main
      |
      v
  [ci.yml] ─── 7 parallel jobs ──> [ci-gate] (summary job)
                                        |
                                        | workflow_run (on CI success, main only)
                                        v
                                  [deploy.yml]
                                        |
                                  ┌─────┴─────┐
                                  v            v
                            build-backend  build-frontend
                            (SHA tag)      (SHA tag)
                                  └─────┬──────┘
                                        v
                                  deploy-nonprod
                                  (environment: nonprod)
                                  (health check)
                                        |
                                        v  (FUTURE)
                                  deploy-production
                                  (environment: production)
                                  (requires manual approval)
                                  (same SHA image)
```

---

## Workflow Files

### `ci.yml` — Continuous Integration

Runs on every push and pull request to `main` and `develop`.

| Job | Purpose |
|-----|---------|
| `backend-lint` | Black, isort, flake8 formatting and linting |
| `backend-test` | pytest with coverage |
| `hr-stabilization-regressions` | HR-specific regression test gates |
| `frontend-lint` | TypeScript check + ESLint |
| `frontend-build` | Next.js production build |
| `dependency-audit` | pip-audit, npm audit, Bandit |
| `security-scan` | CodeQL analysis (Python + JavaScript) |
| **`ci-gate`** | Aggregates all 7 jobs into a single pass/fail status check |

The `ci-gate` job uses `if: always()` and checks each upstream job's result. If any required job fails, `ci-gate` fails, which blocks deployment.

### `deploy.yml` — Deployment

Triggers automatically via `workflow_run` when CI completes successfully on `main`. Also supports `workflow_dispatch` for emergency manual deployments.

| Job | Purpose |
|-----|---------|
| `check-ci` | Resolves commit SHA, verifies CI passed |
| `build-backend` | Builds backend Docker image with SHA tag |
| `build-frontend` | Builds frontend Docker image with SHA tag |
| `deploy-nonprod` | Deploys to NonProd Azure Container Apps, runs health checks |

### `rag-evaluation.yml` — RAG Quality (Independent)

Scheduled weekly evaluation of RAG accuracy. Not a deployment gate.

---

## CI Gate

The `ci-gate` job in `ci.yml` is the single required status check for branch protection. Instead of listing all 7 CI jobs individually, branch protection only needs to require `CI Gate`.

**How it works:**
1. Runs with `if: always()` so it executes even if upstream jobs fail
2. Checks each upstream job's `result` (success/failure/cancelled/skipped)
3. Fails explicitly if any required job reports `failure`
4. This single job becomes the deployment gate via `workflow_run`

---

## Deployment Flow

### Automatic (Normal)

1. Developer pushes to `main` (or PR is merged)
2. `ci.yml` runs all 7 CI jobs in parallel
3. `ci-gate` aggregates results
4. If `ci-gate` passes, `deploy.yml` triggers via `workflow_run`
5. `check-ci` resolves the commit SHA and verifies CI succeeded
6. `build-backend` and `build-frontend` run in parallel, producing SHA-tagged images
7. `deploy-nonprod` deploys the exact SHA-tagged images to Azure Container Apps
8. Health checks verify the deployment

### Manual (Emergency)

1. Navigate to Actions > "Deploy to Azure Container Apps" > Run workflow
2. Optionally set `skip_ci_check` to `true` (logged as a warning)
3. Pipeline builds and deploys from the current `main` HEAD

---

## Image Promotion Strategy

**Build once, deploy many.**

- Every build produces a deterministic image tag: `<registry>/rush-policy-backend:<sha-short>`
- The same `sha_short` tag is used across all environments
- No rebuild between nonprod and production (future)
- The `latest` tag is also applied but is **never used for deployments**

### Traceability

```
Commit SHA  ──>  Image Tag  ──>  Container App Revision
  abc1234         abc1234          rev-abc1234
```

The GitHub Actions deployment summary records the exact image deployed to each environment.

---

## Emergency Procedures

### Manual Deploy (Bypass CI)

```
Actions > Deploy to Azure Container Apps > Run workflow > skip_ci_check: true
```

This is logged with a `::warning` annotation. Use only when CI is flaky but the code is known-good.

### Rollback

Deploy a previous known-good SHA:

```bash
# Find the previous good image tag from deployment history or git log
TAG=<previous-sha-short>

az containerapp update \
  -n rush-policy-backend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-backend:$TAG

az containerapp update \
  -n rush-policy-frontend \
  -g RU-A-NonProd-AI-Innovation-RG \
  --image aiinnovation.azurecr.io/rush-policy-frontend:$TAG
```

Previous SHA tags remain in ACR and can be redeployed at any time.

### Manual CLI Deploy (No GitHub Actions)

See the manual deployment commands in [CLAUDE.md](../CLAUDE.md) under "Container Deployment".

---

## Adding Production

When production Azure resources are provisioned:

1. Create Azure resources in `RU-Azure-Prod` / `RU-A-Prod-AI-Innovation-RG`
2. Create `production` GitHub Environment with required reviewers and deployment branch `main` (see [GITHUB_ENVIRONMENTS_SETUP.md](GITHUB_ENVIRONMENTS_SETUP.md))
3. Add prod `AZURE_CREDENTIALS` and `BACKEND_URL` as `production` environment secrets
4. Uncomment the `deploy-production` job in `deploy.yml`
5. Update the health check URL to the production domain
6. Test with manual dispatch first
7. Update this document to reflect production is active

---

## Audit Compliance

| Requirement | How It's Met |
|-------------|-------------|
| **Traceability** | Commit SHA maps to image tag maps to deployment revision |
| **Gating** | All 7 CI checks must pass before deployment proceeds |
| **Approval** | Future production environment requires manual approval |
| **Immutability** | SHA-tagged images are never overwritten |
| **Visibility** | Deployment summary in GitHub Actions, environment history in Settings |
| **Emergency access** | Manual dispatch with audit trail (`skip_ci_check` warning logged) |

---

## Related Documentation

- [GITHUB_ENVIRONMENTS_SETUP.md](GITHUB_ENVIRONMENTS_SETUP.md) — Environment configuration guide
- [DEPLOYMENT.md](DEPLOYMENT.md) — Manual Azure deployment procedures
- [SECURITY.md](SECURITY.md) — Security architecture including CI/CD security
