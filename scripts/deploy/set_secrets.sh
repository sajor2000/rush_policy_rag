#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <resource-group> <container-app-name> <secrets-file>" >&2
  exit 1
fi

RESOURCE_GROUP=$1
CONTAINER_APP_NAME=$2
SECRETS_FILE=$3

if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "Secrets file $SECRETS_FILE not found" >&2
  exit 1
fi

declare -a SECRET_ARGS
ENV_ARGS=""
while IFS='=' read -r KEY VALUE; do
  [[ -z "$KEY" || "$KEY" =~ ^# ]] && continue
  KEY=$(echo "$KEY" | xargs)
  VALUE=$(echo "$VALUE" | xargs)
  SECRET_ARGS+=("${KEY}=${VALUE}")
  ENV_ARGS+=" ${KEY}=secretref:${KEY}"
done < "$SECRETS_FILE"

if [[ ${#SECRET_ARGS[@]} -eq 0 ]]; then
  echo "No secrets parsed from $SECRETS_FILE" >&2
  exit 1
fi

echo "Storing secrets in Container App ${CONTAINER_APP_NAME}"
# shellcheck disable=SC2068
az containerapp secret set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --secrets ${SECRET_ARGS[@]}

echo "Linking secrets to environment variables"
# shellcheck disable=SC2086
az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --env-vars ${ENV_ARGS}

echo "Secrets configured"
