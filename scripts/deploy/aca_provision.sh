#!/usr/bin/env bash
set -euo pipefail

# Azure Resource Defaults - RU-A-NonProd-AI-Innovation-RG (NonProd subscription)
RESOURCE_GROUP=${RESOURCE_GROUP:-RU-A-NonProd-AI-Innovation-RG}
LOCATION=${LOCATION:-eastus}
ACA_ENVIRONMENT=${ACA_ENVIRONMENT:-rush-policy-env-production}

echo "Ensuring Azure CLI login..."
if ! az account show >/dev/null 2>&1; then
  az login --only-show-errors >/dev/null
fi

echo "Creating/validating resource group ${RESOURCE_GROUP} in ${LOCATION}"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Installing/updating Container Apps extension"
if az extension show --name containerapp >/dev/null 2>&1; then
  az extension update --name containerapp --only-show-errors >/dev/null
else
  az extension add --name containerapp --only-show-errors >/dev/null
fi

if az containerapp env show --name "$ACA_ENVIRONMENT" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Container Apps environment ${ACA_ENVIRONMENT} already exists"
else
  echo "Creating Container Apps environment ${ACA_ENVIRONMENT}"
  az containerapp env create \
    --name "$ACA_ENVIRONMENT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
fi

echo "Provisioning complete."
