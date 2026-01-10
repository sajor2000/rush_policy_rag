#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Azure Resource Defaults - RU-A-NonProd-AI-Innovation-RG (NonProd subscription)
RESOURCE_GROUP=${RESOURCE_GROUP:-RU-A-NonProd-AI-Innovation-RG}
ACA_ENVIRONMENT=${ACA_ENVIRONMENT:-rush-policy-env-production}
CONTAINER_APP_NAME=${CONTAINER_APP_NAME:-rush-policy-backend}
ACR_NAME=${ACR_NAME:-aiinnovation}
IMAGE_NAME=${IMAGE_NAME:-rush-policy-api}
IMAGE_TAG=${IMAGE_TAG:-latest}
TARGET_PORT=${TARGET_PORT:-8000}
CPU=${CPU:-1.0}
MEMORY=${MEMORY:-2Gi}
MIN_REPLICAS=${MIN_REPLICAS:-1}
MAX_REPLICAS=${MAX_REPLICAS:-5}
HTTP_CONCURRENCY=${HTTP_CONCURRENCY:-50}
TAGS=${TAGS:-"project=rush-policy-rag environment=production managed-by=deployment-script"}
LOG_ANALYTICS_WORKSPACE_ID=${LOG_ANALYTICS_WORKSPACE_ID:-}
LOG_ANALYTICS_WORKSPACE_KEY=${LOG_ANALYTICS_WORKSPACE_KEY:-}
EXPOSED=${EXTERNAL_INGRESS:-true}
ENV_VARS=${ENV_VARS:-}
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env"}
AUTO_LOAD_ENV=${AUTO_LOAD_ENV:-true}

IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

# Source common functions and run pre-flight checks
if [[ -f "$SCRIPT_DIR/common-functions.sh" ]]; then
  source "$SCRIPT_DIR/common-functions.sh"
  
  echo "Running pre-flight checks..."
  validate_azure_cli || exit 1
  validate_resource_group "$RESOURCE_GROUP" || exit 1
  # Only check environment if we expect it to exist (update mode) or we want to fail fast
  # For create/update, it's good to check if it exists, but if the script is intended to create it?
  # This script assumes ACA_ENVIRONMENT exists (it doesn't create it). aca_provision.sh creates it.
  validate_container_app_env "$ACA_ENVIRONMENT" "$RESOURCE_GROUP" || exit 1
  echo "Pre-flight checks passed."
fi

# Auto-load environment variables from .env file if enabled and file exists
if [[ "$AUTO_LOAD_ENV" == "true" && -f "$ENV_FILE" ]]; then
  echo "Loading environment variables from $ENV_FILE..."
  
  # Use converter script to parse .env file
  CONVERTER_SCRIPT="$SCRIPT_DIR/convert-env-to-azure.sh"
  if [[ ! -f "$CONVERTER_SCRIPT" ]]; then
    echo "Warning: convert-env-to-azure.sh not found, skipping auto-load" >&2
  else
    # Get converted environment variables
    CONVERTED_ENV=$("$CONVERTER_SCRIPT" "$ENV_FILE" "container-apps" 2>/dev/null || true)
    
    if [[ -n "$CONVERTED_ENV" ]]; then
      # Extract ENV_VARS and SECRET_VARS from converter output
      # Format: ENV_VARS="KEY1=\"value1\" KEY2=\"value2\" ..."
      ENV_VARS_FROM_FILE=$(echo "$CONVERTED_ENV" | grep "^ENV_VARS=" | sed 's/^ENV_VARS="//;s/"$//' || true)
      SECRET_VARS_FROM_FILE=$(echo "$CONVERTED_ENV" | grep "^SECRET_VARS=" | sed 's/^SECRET_VARS="//;s/"$//' || true)
      
      # Merge with existing ENV_VARS if provided
      if [[ -n "$ENV_VARS_FROM_FILE" ]]; then
        if [[ -n "$ENV_VARS" ]]; then
          ENV_VARS="$ENV_VARS $ENV_VARS_FROM_FILE"
        else
          ENV_VARS="$ENV_VARS_FROM_FILE"
        fi
      fi
      
      # Set secrets if any were found
      if [[ -n "$SECRET_VARS_FROM_FILE" ]]; then
        echo "Setting secrets in Container App..."
        # Convert space-separated KEY=VALUE pairs to array
        IFS=' ' read -ra SECRET_PAIRS <<< "$SECRET_VARS_FROM_FILE"
        declare -a SECRET_ARGS
        for pair in "${SECRET_PAIRS[@]}"; do
          # Extract key name for secret reference
          KEY="${pair%%=*}"
          # Remove quotes from value if present
          VALUE="${pair#*=}"
          VALUE="${VALUE#\"}"
          VALUE="${VALUE%\"}"
          SECRET_ARGS+=("${KEY}=${VALUE}")
        done
        
        # Set secrets
        if [[ ${#SECRET_ARGS[@]} -gt 0 ]]; then
          az containerapp secret set \
            --resource-group "$RESOURCE_GROUP" \
            --name "$CONTAINER_APP_NAME" \
            --secrets "${SECRET_ARGS[@]}" \
            --output none 2>/dev/null || {
            echo "Warning: Failed to set secrets. Container App may not exist yet or secrets may already be set." >&2
          }
          
          # Link secrets to environment variables (create secret references)
          declare -a SECRET_ENV_REFS
          for arg in "${SECRET_ARGS[@]}"; do
            KEY="${arg%%=*}"
            SECRET_ENV_REFS+=("${KEY}=secretref:${KEY}")
          done
          
          if [[ ${#SECRET_ENV_REFS[@]} -gt 0 ]]; then
            # Add secret references to ENV_VARS
            if [[ -n "$ENV_VARS" ]]; then
              ENV_VARS="$ENV_VARS ${SECRET_ENV_REFS[*]}"
            else
              ENV_VARS="${SECRET_ENV_REFS[*]}"
            fi
          fi
        fi
      fi
    fi
  fi
fi

COMMON_ARGS=(
  --name "$CONTAINER_APP_NAME"
  --resource-group "$RESOURCE_GROUP"
  --environment "$ACA_ENVIRONMENT"
  --image "$IMAGE"
  --target-port "$TARGET_PORT"
  --ingress "$([[ "$EXPOSED" == true ]] && echo external || echo internal)"
  --transport auto
  --registry-server "${ACR_NAME}.azurecr.io"
  --cpu "$CPU"
  --memory "$MEMORY"
  --min-replicas "$MIN_REPLICAS"
  --max-replicas "$MAX_REPLICAS"
  --scale-rule-name http
  --scale-rule-http-concurrency "$HTTP_CONCURRENCY"
  --tags $TAGS
)

if [[ -n "$ENV_VARS" ]]; then
  # shellcheck disable=SC2206
  COMMON_ARGS+=(--env-vars ${ENV_VARS})
fi

if az containerapp show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Updating existing Container App ${CONTAINER_APP_NAME}"
  az containerapp update "${COMMON_ARGS[@]}" --output none
else
  echo "Creating Container App ${CONTAINER_APP_NAME}"
  az containerapp create "${COMMON_ARGS[@]}" --output none
fi

if [[ -n "$LOG_ANALYTICS_WORKSPACE_ID" && -n "$LOG_ANALYTICS_WORKSPACE_KEY" ]]; then
  echo "Linking Log Analytics workspace"
  az containerapp update \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --logs-destination log-analytics \
    --logs-workspace-id "$LOG_ANALYTICS_WORKSPACE_ID" \
    --logs-workspace-key "$LOG_ANALYTICS_WORKSPACE_KEY" \
    --output none
fi

echo "Container App ${CONTAINER_APP_NAME} ready"
