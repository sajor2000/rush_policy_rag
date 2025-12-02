#!/usr/bin/env bash

# Common utility functions for Azure deployment scripts

validate_azure_cli() {
  if ! command -v az &> /dev/null; then
    echo "Error: Azure CLI not installed" >&2
    return 1
  fi
  
  # Check if logged in by trying to get the current account
  # Use a timeout to avoid hanging if it tries to prompt interactively
  if ! az account show &> /dev/null; then
    echo "Error: Not logged into Azure CLI. Please run 'az login'." >&2
    return 1
  fi
  
  return 0
}

validate_resource_group() {
  local rg="$1"
  echo "Verifying Resource Group '$rg'..."
  if ! az group show --name "$rg" &> /dev/null; then
    echo "Error: Resource Group '$rg' not found." >&2
    return 1
  fi
  return 0
}

validate_container_app_env() {
  local env_name="$1"
  local rg="$2"
  echo "Verifying Container Apps Environment '$env_name'..."
  if ! az containerapp env show --name "$env_name" --resource-group "$rg" &> /dev/null; then
    echo "Error: Container Apps Environment '$env_name' not found in resource group '$rg'." >&2
    return 1
  fi
  return 0
}
