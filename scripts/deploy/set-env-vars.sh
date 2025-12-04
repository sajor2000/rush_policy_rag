#!/usr/bin/env bash
# Helper script to update environment variables for existing Azure deployments
# Supports both Azure Container Apps and Azure Web App for Containers
# Usage: set-env-vars.sh <service-type> <resource-group> <app-name> [env-file]
#   service-type: 'container-apps' or 'webapp-containers'
#   resource-group: Azure resource group name
#   app-name: Container App or Web App name
#   env-file: Path to .env file (default: .env in project root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <service-type> <resource-group> <app-name> [env-file]" >&2
  echo "" >&2
  echo "Arguments:" >&2
  echo "  service-type      'container-apps' or 'webapp-containers'" >&2
  echo "  resource-group    Azure resource group name" >&2
  echo "  app-name          Container App or Web App name" >&2
  echo "  env-file          Path to .env file (default: .env in project root)" >&2
  echo "" >&2
  echo "Examples:" >&2
  echo "  $0 container-apps rush-rg rush-policy-api" >&2
  echo "  $0 webapp-containers rush-rg rush-policy-backend .env.production" >&2
  exit 1
fi

SERVICE_TYPE="$1"
RESOURCE_GROUP="$2"
APP_NAME="$3"
ENV_FILE="${4:-$PROJECT_ROOT/.env}"

if [[ "$SERVICE_TYPE" != "container-apps" && "$SERVICE_TYPE" != "webapp-containers" ]]; then
  echo "Error: service-type must be 'container-apps' or 'webapp-containers'" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: Environment file not found: $ENV_FILE" >&2
  exit 1
fi

echo "================================================================================"
echo "Updating Environment Variables"
echo "================================================================================"
echo "Service Type: $SERVICE_TYPE"
echo "Resource Group: $RESOURCE_GROUP"
echo "App Name: $APP_NAME"
echo "Environment File: $ENV_FILE"
echo ""

# Use converter script to parse .env file
CONVERTER_SCRIPT="$SCRIPT_DIR/convert-env-to-azure.sh"
if [[ ! -f "$CONVERTER_SCRIPT" ]]; then
  echo "Error: convert-env-to-azure.sh not found at $CONVERTER_SCRIPT" >&2
  exit 1
fi

CONVERTED_ENV=$("$CONVERTER_SCRIPT" "$ENV_FILE" "$SERVICE_TYPE" 2>/dev/null || {
  echo "Error: Failed to convert environment file" >&2
  exit 1
})

if [[ -z "$CONVERTED_ENV" ]]; then
  echo "Warning: No environment variables found in $ENV_FILE" >&2
  exit 0
fi

if [[ "$SERVICE_TYPE" == "container-apps" ]]; then
  # Handle Container Apps
  echo "Updating Azure Container App environment variables..."
  
  # Extract ENV_VARS and SECRET_VARS
  ENV_VARS=$(echo "$CONVERTED_ENV" | grep "^ENV_VARS=" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
  SECRET_VARS=$(echo "$CONVERTED_ENV" | grep "^SECRET_VARS=" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
  
  # Set secrets first if any
  if [[ -n "$SECRET_VARS" ]]; then
    echo "Setting secrets..."
    IFS=' ' read -ra SECRET_PAIRS <<< "$SECRET_VARS"
    declare -a SECRET_ARGS
    declare -a SECRET_ENV_REFS
    
    for pair in "${SECRET_PAIRS[@]}"; do
      if [[ "$pair" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        KEY="${BASH_REMATCH[1]}"
        VALUE="${BASH_REMATCH[2]}"
        # Remove surrounding quotes if present
        VALUE="${VALUE#\"}"
        VALUE="${VALUE%\"}"
        SECRET_ARGS+=("${KEY}=${VALUE}")
        SECRET_ENV_REFS+=("${KEY}=secretref:${KEY}")
      fi
    done
    
    if [[ ${#SECRET_ARGS[@]} -gt 0 ]]; then
      az containerapp secret set \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --secrets "${SECRET_ARGS[@]}" \
        --output none
      echo "Secrets configured: ${#SECRET_ARGS[@]} secrets"
    fi
  fi
  
  # Set environment variables (including secret references)
  if [[ -n "$ENV_VARS" || ${#SECRET_ENV_REFS[@]} -gt 0 ]]; then
    echo "Setting environment variables..."
    
    # Combine regular env vars with secret references
    ALL_ENV_VARS="$ENV_VARS"
    if [[ ${#SECRET_ENV_REFS[@]} -gt 0 ]]; then
      if [[ -n "$ALL_ENV_VARS" ]]; then
        ALL_ENV_VARS="$ALL_ENV_VARS ${SECRET_ENV_REFS[*]}"
      else
        ALL_ENV_VARS="${SECRET_ENV_REFS[*]}"
      fi
    fi
    
    if [[ -n "$ALL_ENV_VARS" ]]; then
      az containerapp update \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --env-vars ${ALL_ENV_VARS} \
        --output none
      echo "Environment variables configured"
    fi
  fi
  
else
  # Handle Web App for Containers
  echo "Updating Azure Web App application settings..."
  
  APP_SETTINGS=$(echo "$CONVERTED_ENV" | grep "^APP_SETTINGS=" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
  
  if [[ -n "$APP_SETTINGS" ]]; then
    # Parse settings into array
    declare -a SETTINGS_ARRAY
    IFS=' ' read -ra SETTING_PAIRS <<< "$APP_SETTINGS"
    
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
    
    if [[ ${#SETTINGS_ARRAY[@]} -gt 0 ]]; then
      az webapp config appsettings set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --settings "${SETTINGS_ARRAY[@]}" \
        --output none
      echo "Application settings configured: ${#SETTINGS_ARRAY[@]} settings"
    fi
  fi
fi

echo ""
echo "================================================================================"
echo "Environment variables updated successfully!"
echo "================================================================================"
echo ""
echo "To verify, check the configuration:"
if [[ "$SERVICE_TYPE" == "container-apps" ]]; then
  echo "  az containerapp show --name $APP_NAME --resource-group $RESOURCE_GROUP --query 'properties.template.containers[0].env'"
else
  echo "  az webapp config appsettings list --name $APP_NAME --resource-group $RESOURCE_GROUP"
fi
echo ""



