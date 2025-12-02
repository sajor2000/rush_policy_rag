#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP=${RESOURCE_GROUP:-rush-rg}
CONTAINER_APP_NAME=${CONTAINER_APP_NAME:-rush-policy-api}
STORAGE_SCOPE=${STORAGE_SCOPE:-}
SEARCH_SCOPE=${SEARCH_SCOPE:-}

if [[ -z "$STORAGE_SCOPE" || -z "$SEARCH_SCOPE" ]]; then
  echo "STORAGE_SCOPE and SEARCH_SCOPE must be provided (resource IDs)" >&2
  exit 1
fi

PRINCIPAL_ID=$(az containerapp identity assign \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --output tsv \
  --query 'principalId')

echo "Assigned managed identity ${PRINCIPAL_ID}"

echo "Granting Storage Blob Data Reader"
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Storage Blob Data Reader" \
  --scope "$STORAGE_SCOPE" \
  --output none || true

echo "Granting Search Index Data Contributor"
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Search Index Data Contributor" \
  --scope "$SEARCH_SCOPE" \
  --output none || true

echo "Role assignments complete"
