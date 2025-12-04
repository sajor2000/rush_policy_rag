#!/usr/bin/env bash
# Fix ZIP deploy issues for Azure Web App Service
# This script addresses common ZIP deploy failures
# Usage: fix-zip-deploy.sh <resource-group> <app-name>

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <resource-group> <app-name>" >&2
  echo "" >&2
  echo "This script fixes common ZIP deploy issues:" >&2
  echo "  1. Sets correct Python version (3.11 recommended)" >&2
  echo "  2. Configures startup command for FastAPI" >&2
  echo "  3. Sets required app settings" >&2
  exit 1
fi

RESOURCE_GROUP="$1"
APP_NAME="$2"
PYTHON_VERSION="3.11"  # Azure Web App Service recommended version

echo "================================================================================"
echo "Fixing ZIP Deploy Configuration for Azure Web App Service"
echo "================================================================================"
echo "Resource Group: $RESOURCE_GROUP"
echo "App Name: $APP_NAME"
echo "Python Version: $PYTHON_VERSION"
echo ""

# Check if Web App exists
if ! az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Error: Web App '$APP_NAME' not found in resource group '$RESOURCE_GROUP'" >&2
  exit 1
fi

echo "Step 1: Setting Python version to $PYTHON_VERSION..."
az webapp config set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --linux-fx-version "PYTHON|$PYTHON_VERSION" \
  --output none

echo "✓ Python version configured"

echo ""
echo "Step 2: Configuring startup command for FastAPI..."
az webapp config set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000" \
  --output none

echo "✓ Startup command configured"

echo ""
echo "Step 3: Setting required app settings..."
az webapp config appsettings set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --settings \
    WEBSITES_PORT=8000 \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true \
    POST_BUILD_COMMAND="" \
    PRE_BUILD_COMMAND="" \
  --output none

echo "✓ App settings configured"

echo ""
echo "Step 4: Verifying requirements.txt exists..."
# Check if we can see the app structure
WORKING_DIR=$(pwd)
if [[ -f "apps/backend/requirements.txt" ]]; then
  echo "✓ requirements.txt found at apps/backend/requirements.txt"
  echo "  Make sure this file is included in your ZIP deployment"
else
  echo "⚠ Warning: requirements.txt not found at apps/backend/requirements.txt"
  echo "  Ensure requirements.txt is in the root of your ZIP file"
fi

echo ""
echo "================================================================================"
echo "Configuration Complete!"
echo "================================================================================"
echo ""
echo "Next steps:"
echo "1. Create a new ZIP file with the correct structure:"
echo "   cd apps/backend"
echo "   zip -r deploy.zip . -x \"*.pyc\" -x \"__pycache__/*\" -x \".env\" -x \"venv/*\" -x \".venv/*\""
echo ""
echo "2. Deploy the ZIP file:"
echo "   az webapp deployment source config-zip \\"
echo "     --name $APP_NAME \\"
echo "     --resource-group $RESOURCE_GROUP \\"
echo "     --src deploy.zip"
echo ""
echo "3. Check deployment logs:"
echo "   az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP"
echo ""
echo "4. If ZIP deploy continues to fail, consider switching to:"
echo "   - Azure Web App for Containers (Docker): More reliable for FastAPI"
echo "   - Azure Container Apps: Recommended for containerized apps"
echo ""



