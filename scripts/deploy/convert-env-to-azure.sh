#!/usr/bin/env bash
# Convert .env file to Azure CLI format for Container Apps or Web App for Containers
# Usage: convert-env-to-azure.sh <env-file> <output-format> [sensitive-vars-file]
#   output-format: 'container-apps' or 'webapp-containers'
#   sensitive-vars-file: Optional file listing which vars should be secrets (one per line)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <env-file> <output-format> [sensitive-vars-file]" >&2
  echo "  output-format: 'container-apps' or 'webapp-containers'" >&2
  echo "  sensitive-vars-file: Optional file listing sensitive variable names (one per line)" >&2
  exit 1
fi

ENV_FILE="$1"
OUTPUT_FORMAT="$2"
SENSITIVE_VARS_FILE="${3:-}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: Environment file not found: $ENV_FILE" >&2
  exit 1
fi

if [[ "$OUTPUT_FORMAT" != "container-apps" && "$OUTPUT_FORMAT" != "webapp-containers" ]]; then
  echo "Error: output-format must be 'container-apps' or 'webapp-containers'" >&2
  exit 1
fi

# Default list of sensitive variables (API keys, connection strings, etc.)
declare -a SENSITIVE_VARS=(
  "SEARCH_API_KEY"
  "AOAI_API_KEY"
  "AOAI_API"
  "COHERE_RERANK_API_KEY"
  "ADMIN_API_KEY"
  "STORAGE_CONNECTION_STRING"
  "APPLICATIONINSIGHTS_CONNECTION_STRING"
  "AZURE_AD_TENANT_ID"
  "AZURE_AD_CLIENT_ID"
  "AZURE_AD_CLIENT_SECRET"
  "REGISTRY_PASSWORD"
  "GITHUB_TOKEN"
)

# Load additional sensitive vars from file if provided
if [[ -n "$SENSITIVE_VARS_FILE" && -f "$SENSITIVE_VARS_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    SENSITIVE_VARS+=("$line")
  done < "$SENSITIVE_VARS_FILE"
fi

# Function to check if a variable is sensitive
is_sensitive() {
  local var_name="$1"
  for sensitive in "${SENSITIVE_VARS[@]}"; do
    if [[ "$var_name" == "$sensitive" ]]; then
      return 0
    fi
  done
  return 1
}

# Function to escape value for shell (preserve spaces and special chars)
escape_value() {
  local value="$1"
  # Don't escape - we'll quote the entire value instead
  echo "$value"
}

# Parse .env file
declare -a ENV_VARS_ARRAY
declare -a SECRET_VARS_ARRAY
ENV_VARS_ARRAY=()
SECRET_VARS_ARRAY=()

while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip empty lines and comments
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  
  # Remove leading/trailing whitespace
  line=$(echo "$line" | xargs)
  [[ -z "$line" ]] && continue
  
  # Skip lines that don't look like KEY=VALUE
  if [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
    continue
  fi
  
  # Extract key and value
  KEY="${line%%=*}"
  VALUE="${line#*=}"
  
  # Remove quotes if present
  if [[ "$VALUE" =~ ^\".*\"$ ]] || [[ "$VALUE" =~ ^\'.*\'$ ]]; then
    VALUE="${VALUE:1:-1}"
  fi
  
  # Skip if key or value is empty
  [[ -z "$KEY" ]] && continue
  
  # Use the value as-is (will be quoted when output)
  if is_sensitive "$KEY"; then
    if [[ "$OUTPUT_FORMAT" == "container-apps" ]]; then
      # For Container Apps, sensitive vars go to secrets (store raw value)
      SECRET_VARS_ARRAY+=("${KEY}=${VALUE}")
    else
      # For Web App, all vars go to settings (Azure handles encryption)
      # Format: KEY="VALUE" (quoted for shell parsing)
      ENV_VARS_ARRAY+=("${KEY}=\"${VALUE}\"")
    fi
  else
    # Non-sensitive vars - format: KEY="VALUE"
    ENV_VARS_ARRAY+=("${KEY}=\"${VALUE}\"")
  fi
done < "$ENV_FILE"

# Output results
# Use printf to join array elements with spaces, preserving quoted values
ENV_COUNT=0
SECRET_COUNT=0
if [[ ${ENV_VARS_ARRAY+x} ]]; then
  ENV_COUNT=${#ENV_VARS_ARRAY[@]}
fi
if [[ ${SECRET_VARS_ARRAY+x} ]]; then
  SECRET_COUNT=${#SECRET_VARS_ARRAY[@]}
fi

if [[ "$OUTPUT_FORMAT" == "container-apps" ]]; then
  # For Container Apps: output env-vars and secrets separately
  if [[ $ENV_COUNT -gt 0 ]]; then
    echo "# Environment variables (non-sensitive)"
    printf -v ENV_VARS_STR '%s ' "${ENV_VARS_ARRAY[@]}"
    echo "ENV_VARS=\"${ENV_VARS_STR%% }\""
  fi
  
  if [[ $SECRET_COUNT -gt 0 ]]; then
    echo ""
    echo "# Secrets (sensitive variables)"
    printf -v SECRET_VARS_STR '%s ' "${SECRET_VARS_ARRAY[@]}"
    echo "SECRET_VARS=\"${SECRET_VARS_STR%% }\""
  fi
else
  # For Web App for Containers: all vars go to --settings
  if [[ $ENV_COUNT -gt 0 || $SECRET_COUNT -gt 0 ]]; then
    ALL_VARS=()
    if [[ $ENV_COUNT -gt 0 ]]; then
      ALL_VARS+=("${ENV_VARS_ARRAY[@]}")
    fi
    if [[ $SECRET_COUNT -gt 0 ]]; then
      ALL_VARS+=("${SECRET_VARS_ARRAY[@]}")
    fi
    echo "# App settings (all variables)"
    printf -v APP_SETTINGS_STR '%s ' "${ALL_VARS[@]}"
    echo "APP_SETTINGS=\"${APP_SETTINGS_STR%% }\""
  fi
fi

