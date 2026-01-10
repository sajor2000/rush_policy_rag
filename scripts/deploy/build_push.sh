#!/usr/bin/env bash
set -euo pipefail

# Azure Resource Defaults - RU-A-NonProd-AI-Innovation-RG (NonProd subscription)
RESOURCE_GROUP=${RESOURCE_GROUP:-RU-A-NonProd-AI-Innovation-RG}
ACR_NAME=${ACR_NAME:-aiinnovation}
IMAGE_NAME=${IMAGE_NAME:-rush-policy-backend}
IMAGE_TAG=${IMAGE_TAG:-latest}
CONTEXT_PATH=${CONTEXT_PATH:-apps/backend}
DOCKERFILE=${DOCKERFILE:-apps/backend/Dockerfile}

FULL_IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

echo "Ensuring Azure Container Registry ${ACR_NAME} exists"
if ! az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --sku Basic --output none
fi

echo "Logging into ${ACR_NAME}"
az acr login --name "$ACR_NAME" >/dev/null

echo "Building Docker image ${FULL_IMAGE}"
docker build -f "$DOCKERFILE" -t "$FULL_IMAGE" "$CONTEXT_PATH"

echo "Pushing ${FULL_IMAGE}"
docker push "$FULL_IMAGE"

echo "Image push complete"
