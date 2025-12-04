# ZIP Deploy Troubleshooting Guide

This guide helps diagnose and fix ZIP deployment failures for Azure Web App Service.

## Quick Diagnosis

Run the analysis script to identify issues:

```bash
./scripts/deploy/analyze-zip-deploy-issues.sh <resource-group> <app-name> [zip-file]
```

## Common ZIP Deploy Issues

### Issue 1: Python Version Too New

**Symptom**: Deployment fails with Python 3.13.8 or 3.12.x

**Cause**: Azure Web App Service may not fully support very new Python versions

**Solution**:
```bash
# Set to Python 3.11 (recommended, well-supported)
az webapp config set \
  --name <app-name> \
  --resource-group <resource-group> \
  --linux-fx-version "PYTHON|3.11"
```

### Issue 2: Missing Startup Command

**Symptom**: App fails to start, shows "Application Error"

**Cause**: FastAPI requires explicit startup command (Azure doesn't auto-detect ASGI apps)

**Solution**:
```bash
az webapp config set \
  --name <app-name> \
  --resource-group <resource-group> \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000"
```

### Issue 3: Missing Required Files in ZIP

**Symptom**: Build fails, "module not found" errors

**Required Files**:
- `main.py` (entry point)
- `requirements.txt` (dependencies)
- `app/` directory (application code)

**Solution**: Use the automated ZIP creation script:
```bash
./scripts/deploy/create-zip-deploy.sh
```

### Issue 4: ZIP File Too Large

**Symptom**: Deployment fails with size-related errors

**Cause**: Azure has a 100MB limit for ZIP deploy

**Solution**:
1. Exclude unnecessary files (tests, data, venv, etc.)
2. Use the automated script which excludes large files
3. Consider switching to container deployment

### Issue 5: Missing App Settings

**Symptom**: App starts but doesn't work correctly

**Required Settings**:
- `WEBSITES_PORT=8000` (tells Azure which port to use)

**Solution**:
```bash
az webapp config appsettings set \
  --name <app-name> \
  --resource-group <resource-group> \
  --settings WEBSITES_PORT=8000
```

### Issue 6: Incorrect File Structure

**Symptom**: Import errors, "module not found"

**Correct Structure**:
```
deploy.zip
├── main.py              # Entry point
├── requirements.txt     # Dependencies
├── app/                 # Application code
│   ├── __init__.py
│   ├── core/
│   ├── api/
│   └── ...
└── (other necessary files)
```

**Solution**: Ensure ZIP is created from `apps/backend/` directory

### Issue 7: Missing Dependencies in requirements.txt

**Symptom**: "No module named 'uvicorn'" or similar errors

**Required Dependencies**:
- `fastapi>=0.109.0`
- `uvicorn[standard]>=0.27.0`
- `gunicorn>=21.2.0` (for production)

**Solution**: Verify `requirements.txt` includes all dependencies

## Step-by-Step Fix Process

### Step 1: Analyze Current Issues

```bash
./scripts/deploy/analyze-zip-deploy-issues.sh <resource-group> <app-name>
```

This will show:
- Current Python version
- Startup command status
- App settings
- Recent deployment status
- ZIP file analysis (if provided)

### Step 2: Fix Configuration

```bash
# Fix all common configuration issues at once
./scripts/deploy/fix-zip-deploy.sh <resource-group> <app-name>
```

This script:
- Sets Python to 3.11
- Configures FastAPI startup command
- Sets required app settings

### Step 3: Create Proper ZIP Package

```bash
# Create validated ZIP package
./scripts/deploy/create-zip-deploy.sh
```

This script:
- Validates required files exist
- Excludes unnecessary files
- Checks for large files
- Creates optimized ZIP

### Step 4: Deploy

```bash
az webapp deployment source config-zip \
  --name <app-name> \
  --resource-group <resource-group> \
  --src apps/backend/deploy.zip
```

### Step 5: Monitor Deployment

```bash
# Watch deployment logs
az webapp log tail --name <app-name> --resource-group <resource-group>

# Check app status
az webapp show --name <app-name> --resource-group <resource-group> --query "state"
```

## ZIP File Best Practices

### Files to Include:
- ✅ `main.py` (entry point)
- ✅ `requirements.txt` (dependencies)
- ✅ `app/` directory (application code)
- ✅ All Python source files (`.py`)
- ✅ Configuration files (if needed)

### Files to Exclude:
- ❌ `__pycache__/` (Python cache)
- ❌ `*.pyc`, `*.pyo` (compiled Python)
- ❌ `venv/`, `.venv/` (virtual environments)
- ❌ `.env` (environment variables - set via Azure)
- ❌ `tests/` (test files)
- ❌ `data/test_pdfs/` (test data)
- ❌ `evaluation/` (evaluation scripts)
- ❌ `Dockerfile` (not needed for ZIP deploy)
- ❌ `.git/` (version control)
- ❌ Large data files (>10MB)

### ZIP Creation Command:

```bash
cd apps/backend

zip -r deploy.zip . \
  -x "*.pyc" \
  -x "*__pycache__/*" \
  -x ".env" \
  -x "venv/*" \
  -x ".venv/*" \
  -x "tests/*" \
  -x "data/test_pdfs/*" \
  -x "evaluation/*" \
  -x "Dockerfile" \
  -x ".git/*"
```

## When to Switch to Container Deployment

ZIP deploy can be unreliable for FastAPI. Consider switching if:

1. ✅ ZIP deploy keeps failing after fixes
2. ✅ You need more control over the runtime environment
3. ✅ You want to use Docker (already have Dockerfile)
4. ✅ You need better deployment reliability

**Options**:

1. **Azure Web App for Containers** (Docker):
   ```bash
   ./scripts/deploy/webapp_containers_deploy.sh
   ```

2. **Azure Container Apps** (Recommended):
   ```bash
   ./scripts/deploy/aca_deploy.sh
   ```

Both options:
- Use your existing Dockerfile
- Support automatic `.env` file loading
- Are more reliable for FastAPI/ASGI apps
- Better suited for production

## Troubleshooting Commands

```bash
# Check Python version
az webapp config show --name <app-name> --resource-group <rg> --query "linuxFxVersion"

# Check startup command
az webapp config show --name <app-name> --resource-group <rg> --query "appCommandLine"

# List app settings
az webapp config appsettings list --name <app-name> --resource-group <rg>

# View deployment history
az webapp deployment list --name <app-name> --resource-group <rg>

# Download logs
az webapp log download --name <app-name> --resource-group <rg> --log-file logs.zip

# Stream logs
az webapp log tail --name <app-name> --resource-group <rg>

# Check app state
az webapp show --name <app-name> --resource-group <rg> --query "{state:state,defaultHostName:defaultHostName}"
```

## Summary

1. **Always use the analysis script first**: `analyze-zip-deploy-issues.sh`
2. **Fix configuration**: `fix-zip-deploy.sh`
3. **Create proper ZIP**: `create-zip-deploy.sh`
4. **Deploy and monitor**: Watch logs for errors
5. **If still failing**: Consider switching to container deployment

For more details, see [AZURE_WEBAPP_FASTAPI_DEPLOYMENT.md](AZURE_WEBAPP_FASTAPI_DEPLOYMENT.md).



