#!/usr/bin/env bash
# Deploy Docker container to Azure Web App for Containers with automatic .env file loading
# Usage: webapp_containers_deploy.sh [options]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Configuration variables
RESOURCE_GROUP=${RESOURCE_GROUP:-rush-rg}
APP_NAME=${APP_NAME:-rush-policy-backend}
APP_SERVICE_PLAN=${APP_SERVICE_PLAN:-asp-rush-policy-prod}
ACR_NAME=${ACR_NAME:-rushacr}
IMAGE_NAME=${IMAGE_NAME:-rush-policy-api}
IMAGE_TAG=${IMAGE_TAG:-latest}
TARGET_PORT=${TARGET_PORT:-8000}
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env"}
AUTO_LOAD_ENV=${AUTO_LOAD_ENV:-true}
STARTUP_COMMAND=${STARTUP_COMMAND:-"uvicorn main:app --host 0.0.0.0 --port 8000"}
TAGS=${TAGS:-"project=rush-policy-rag environment=production managed-by=deployment-script"}

IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

# Source common functions and run pre-flight checks
if [[ -f "$SCRIPT_DIR/common-functions.sh" ]]; then
  source "$SCRIPT_DIR/common-functions.sh"
  
  echo "Running pre-flight checks..."
  validate_azure_cli || exit 1
  validate_resource_group "$RESOURCE_GROUP" || exit 1
  echo "Pre-flight checks passed."
fi

echo "================================================================================"
echo "Azure Web App for Containers Deployment"
echo "================================================================================"
echo "Resource Group: $RESOURCE_GROUP"
echo "App Name: $APP_NAME"
echo "Image: $IMAGE"
echo ""

# Check if Web App exists
APP_EXISTS=$(az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" 2>/dev/null || echo "")

if [[ -z "$APP_EXISTS" ]]; then
  echo "Creating Web App for Containers..."
  
  # Check if App Service Plan exists
  PLAN_EXISTS=$(az appservice plan show --name "$APP_SERVICE_PLAN" --resource-group "$RESOURCE_GROUP" 2>/dev/null || echo "")
  
  if [[ -z "$PLAN_EXISTS" ]]; then
    echo "Creating App Service Plan: $APP_SERVICE_PLAN"
    az appservice plan create \
      --name "$APP_SERVICE_PLAN" \
      --resource-group "$RESOURCE_GROUP" \
      --is-linux \
      --sku B2 \
      --tags $TAGS \
      --output none
  fi
  
  # Create Web App
  az webapp create \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$APP_SERVICE_PLAN" \
    --deployment-container-image-name "$IMAGE" \
    --tags $TAGS \
    --output none
  
  echo "Web App created successfully"
else
  echo "Web App already exists, updating configuration..."
  az webapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --tags $TAGS --output none
fi

# Configure container registry if needed
echo "Configuring container registry..."
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv 2>/dev/null || echo "")
if [[ -n "$ACR_PASSWORD" ]]; then
  az webapp config container set \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --docker-custom-image-name "$IMAGE" \
    --docker-registry-server-url "https://${ACR_NAME}.azurecr.io" \
    --docker-registry-server-user "$ACR_NAME" \
    --docker-registry-server-password "$ACR_PASSWORD" \
    --output none
fi

# Set startup command
echo "Setting startup command..."
az webapp config set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --startup-file "$STARTUP_COMMAND" \
  --output none

# Set port
echo "Setting port configuration..."
az webapp config appsettings set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --settings WEBSITES_PORT="$TARGET_PORT" \
  --output none

# Auto-load environment variables from .env file if enabled
if [[ "$AUTO_LOAD_ENV" == "true" && -f "$ENV_FILE" ]]; then
  echo "Loading environment variables from $ENV_FILE..."
  
  CONVERTER_SCRIPT="$SCRIPT_DIR/convert-env-to-azure.sh"
  if [[ ! -f "$CONVERTER_SCRIPT" ]]; then
    echo "Warning: convert-env-to-azure.sh not found, skipping auto-load" >&2
  else
    # Get converted environment variables for Web App format
    CONVERTED_ENV=$("$CONVERTER_SCRIPT" "$ENV_FILE" "webapp-containers" 2>/dev/null || true)
    
    if [[ -n "$CONVERTED_ENV" ]]; then
      # Extract APP_SETTINGS from converter output
      # Format: APP_SETTINGS="KEY1=\"value1\" KEY2=\"value2\" ..."
      APP_SETTINGS_FROM_FILE=$(echo "$CONVERTED_ENV" | grep "^APP_SETTINGS=" | sed 's/^APP_SETTINGS="//;s/"$//' || true)
      
      if [[ -n "$APP_SETTINGS_FROM_FILE" ]]; then
        echo "Setting application settings..."
        
        # Parse the settings string into key-value pairs
        # Format: KEY1="value1" KEY2="value2" ...
        declare -a SETTINGS_ARRAY
        
        # Use Python's shlex to properly parse shell-quoted strings safely without eval
        # This handles spaces and quotes correctly while avoiding code execution risks
        if command -v python3 &>/dev/null; then
          while IFS= read -r pair; do
            if [[ -n "$pair" ]]; then
              SETTINGS_ARRAY+=("$pair")
            fi
          done < <(python3 -c "import shlex, sys; print('\n'.join(shlex.split(sys.argv[1])))" "$APP_SETTINGS_FROM_FILE")
        else
          # Fallback if python3 is not available (less robust for spaces)
          echo "Warning: python3 not found, falling back to simple splitting (may fail with spaces)" >&2
          IFS=' ' read -ra SETTING_PAIRS <<< "$APP_SETTINGS_FROM_FILE"
          for pair in "${SETTING_PAIRS[@]}"; do
            if [[ "$pair" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
              KEY="${BASH_REMATCH[1]}"
              VALUE="${BASH_REMATCH[2]}"
              # Remove surrounding quotes if present
              VALUE="${VALUE#\"}"
              VALUE="${VALUE%\"}"
              SETTINGS_ARRAY+=("${KEY}=${VALUE}")
            fi
          done
        fi
        
        # Set all settings at once
        if [[ ${#SETTINGS_ARRAY[@]} -gt 0 ]]; then
          az webapp config appsettings set \
            --name "$APP_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --settings "${SETTINGS_ARRAY[@]}" \
            --output none
          echo "Environment variables configured successfully"
        fi
      fi
    fi
  fi
else
  echo "Skipping automatic environment variable loading (AUTO_LOAD_ENV=false or .env file not found)"
fi

echo ""
echo "================================================================================"
echo "Deployment complete!"
echo "================================================================================"
echo "Web App URL: https://${APP_NAME}.azurewebsites.net"
echo ""
echo "To view logs:"
echo "  az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP"
echo ""
echo "To check health:"
echo "  curl https://${APP_NAME}.azurewebsites.net/health"
echo ""

