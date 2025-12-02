#!/usr/bin/env bash
# Analyze ZIP deploy issues and provide diagnostic information
# Usage: analyze-zip-deploy-issues.sh <resource-group> <app-name> [zip-file]

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <resource-group> <app-name> [zip-file]" >&2
  exit 1
fi

RESOURCE_GROUP="$1"
APP_NAME="$2"
ZIP_FILE="${3:-}"

echo "================================================================================"
echo "ZIP Deploy Issue Analysis"
echo "================================================================================"
echo "Resource Group: $RESOURCE_GROUP"
echo "App Name: $APP_NAME"
echo ""

# Check if Web App exists
if ! az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "❌ ERROR: Web App '$APP_NAME' not found" >&2
  exit 1
fi

echo "Step 1: Checking Web App Configuration..."
echo ""

# Check Python version
PYTHON_VERSION=$(az webapp config show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "linuxFxVersion" -o tsv 2>/dev/null || echo "unknown")

echo "Python Version: $PYTHON_VERSION"
if [[ "$PYTHON_VERSION" == *"3.13"* ]] || [[ "$PYTHON_VERSION" == *"3.12"* ]]; then
  echo "⚠ WARNING: Python $PYTHON_VERSION may not be fully supported"
  echo "  Recommended: Python 3.11"
fi

# Check startup command
STARTUP_CMD=$(az webapp config show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "appCommandLine" -o tsv 2>/dev/null || echo "not set")

echo "Startup Command: ${STARTUP_CMD:-'NOT SET (CRITICAL)'}"
if [[ -z "$STARTUP_CMD" ]]; then
  echo "❌ ERROR: Startup command is not set!"
  echo "  FastAPI requires explicit startup command"
fi

# Check app settings
echo ""
echo "Step 2: Checking App Settings..."
WEBSITES_PORT=$(az webapp config appsettings list \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[?name=='WEBSITES_PORT'].value" -o tsv 2>/dev/null || echo "")

echo "WEBSITES_PORT: ${WEBSITES_PORT:-'NOT SET'}"
if [[ -z "$WEBSITES_PORT" ]]; then
  echo "⚠ WARNING: WEBSITES_PORT not set (should be 8000 for FastAPI)"
fi

SCM_BUILD=$(az webapp config appsettings list \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[?name=='SCM_DO_BUILD_DURING_DEPLOYMENT'].value" -o tsv 2>/dev/null || echo "")

echo "SCM_DO_BUILD_DURING_DEPLOYMENT: ${SCM_BUILD:-'NOT SET (default: true)'}"

# Check recent deployment logs
echo ""
echo "Step 3: Checking Recent Deployment Status..."
DEPLOYMENTS=$(az webapp deployment list \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0:3].{Id:id, Status:status, Active:active, Author:author}" -o table 2>/dev/null || echo "Unable to fetch deployments")

if [[ "$DEPLOYMENTS" != "Unable to fetch deployments" ]]; then
  echo "$DEPLOYMENTS"
else
  echo "⚠ Could not fetch deployment history"
fi

# Analyze ZIP file if provided
if [[ -n "$ZIP_FILE" && -f "$ZIP_FILE" ]]; then
  echo ""
  echo "Step 4: Analyzing ZIP File..."
  ZIP_SIZE=$(du -h "$ZIP_FILE" | cut -f1)
  ZIP_SIZE_BYTES=$(stat -f%z "$ZIP_FILE" 2>/dev/null || stat -c%s "$ZIP_FILE" 2>/dev/null)
  MAX_SIZE=$((100 * 1024 * 1024))  # 100MB
  
  echo "ZIP File: $ZIP_FILE"
  echo "Size: $ZIP_SIZE"
  
  if [[ $ZIP_SIZE_BYTES -gt $MAX_SIZE ]]; then
    echo "❌ ERROR: ZIP file exceeds 100MB limit!"
  fi
  
  # Check for required files
  echo ""
  echo "Checking ZIP contents..."
  if unzip -l "$ZIP_FILE" 2>/dev/null | grep -q "main.py"; then
    echo "✓ main.py found in ZIP"
  else
    echo "❌ main.py NOT found in ZIP"
  fi
  
  if unzip -l "$ZIP_FILE" 2>/dev/null | grep -q "requirements.txt"; then
    echo "✓ requirements.txt found in ZIP"
  else
    echo "❌ requirements.txt NOT found in ZIP"
  fi
  
  if unzip -l "$ZIP_FILE" 2>/dev/null | grep -q "app/"; then
    echo "✓ app/ directory found in ZIP"
  else
    echo "❌ app/ directory NOT found in ZIP"
  fi
  
  # Check for problematic files
  PROBLEMATIC=$(unzip -l "$ZIP_FILE" 2>/dev/null | grep -E "(\.env|venv/|__pycache__|\.git)" | head -5 || true)
  if [[ -n "$PROBLEMATIC" ]]; then
    echo ""
    echo "⚠ WARNING: Potentially problematic files in ZIP:"
    echo "$PROBLEMATIC" | sed 's/^/  /'
  fi
fi

# Get recent logs
echo ""
echo "Step 5: Recent Log Entries (last 20 lines)..."
echo "---"
az webapp log tail \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --only-show-errors 2>/dev/null | tail -20 || echo "Unable to fetch logs (may need to enable logging)"

echo ""
echo "================================================================================"
echo "Analysis Complete"
echo "================================================================================"
echo ""
echo "Common Issues Found:"
echo ""

ISSUES=0

if [[ "$PYTHON_VERSION" == *"3.13"* ]] || [[ "$PYTHON_VERSION" == *"3.12"* ]]; then
  echo "❌ Issue $((++ISSUES)): Python version may not be supported"
  echo "   Fix: az webapp config set --name $APP_NAME --resource-group $RESOURCE_GROUP --linux-fx-version 'PYTHON|3.11'"
fi

if [[ -z "$STARTUP_CMD" ]]; then
  echo "❌ Issue $((++ISSUES)): Startup command not set"
  echo "   Fix: az webapp config set --name $APP_NAME --resource-group $RESOURCE_GROUP --startup-file 'gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000'"
fi

if [[ -z "$WEBSITES_PORT" ]]; then
  echo "⚠ Issue $((++ISSUES)): WEBSITES_PORT not set"
  echo "   Fix: az webapp config appsettings set --name $APP_NAME --resource-group $RESOURCE_GROUP --settings WEBSITES_PORT=8000"
fi

if [[ $ISSUES -eq 0 ]]; then
  echo "✓ No obvious configuration issues found"
  echo ""
  echo "If deployment still fails, check:"
  echo "  1. ZIP file structure (use create-zip-deploy.sh to create proper ZIP)"
  echo "  2. requirements.txt dependencies"
  echo "  3. Application logs for runtime errors"
  echo "  4. Consider switching to container-based deployment"
fi

echo ""
echo "Quick Fix: Run ./scripts/deploy/fix-zip-deploy.sh $RESOURCE_GROUP $APP_NAME"
echo ""

