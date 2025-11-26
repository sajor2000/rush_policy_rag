#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP=${RESOURCE_GROUP:-rush-rg}
ACA_ENVIRONMENT=${ACA_ENVIRONMENT:-rush-aca-env}
CONTAINER_APP_NAME=${CONTAINER_APP_NAME:-rush-policy-api}
ACR_NAME=${ACR_NAME:-rushacr}
IMAGE_NAME=${IMAGE_NAME:-rush-policy-api}
IMAGE_TAG=${IMAGE_TAG:-latest}
TARGET_PORT=${TARGET_PORT:-8000}
CPU=${CPU:-1.0}
MEMORY=${MEMORY:-2Gi}
MIN_REPLICAS=${MIN_REPLICAS:-1}
MAX_REPLICAS=${MAX_REPLICAS:-5}
HTTP_CONCURRENCY=${HTTP_CONCURRENCY:-50}
LOG_ANALYTICS_WORKSPACE_ID=${LOG_ANALYTICS_WORKSPACE_ID:-}
LOG_ANALYTICS_WORKSPACE_KEY=${LOG_ANALYTICS_WORKSPACE_KEY:-}
EXPOSED=${EXTERNAL_INGRESS:-true}
ENV_VARS=${ENV_VARS:-}

IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

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
