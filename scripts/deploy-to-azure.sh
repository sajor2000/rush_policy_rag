#!/bin/bash

# =============================================================================
# RUSH Policy RAG - Azure Deployment Script
# =============================================================================
# This script deploys the backend to Azure Container Apps
# Usage: ./deploy-to-azure.sh <environment> <image-tag>
# Example: ./deploy-to-azure.sh production sha-abc123

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
ENVIRONMENT=${1:-staging}
IMAGE_TAG=${2:-latest}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(dev|staging|production)$ ]]; then
    echo -e "${RED}Error: Environment must be dev, staging, or production${NC}"
    exit 1
fi

echo -e "${GREEN}==============================================================================${NC}"
echo -e "${GREEN}RUSH Policy RAG - Deploying to Azure${NC}"
echo -e "${GREEN}==============================================================================${NC}"
echo -e "Environment: ${YELLOW}$ENVIRONMENT${NC}"
echo -e "Image Tag: ${YELLOW}$IMAGE_TAG${NC}"
echo ""

# Load environment-specific variables
if [ "$ENVIRONMENT" = "production" ]; then
    RESOURCE_GROUP="${AZURE_RESOURCE_GROUP_PROD}"
    MIN_REPLICAS=2
    MAX_REPLICAS=10
elif [ "$ENVIRONMENT" = "staging" ]; then
    RESOURCE_GROUP="${AZURE_RESOURCE_GROUP_STAGING}"
    MIN_REPLICAS=1
    MAX_REPLICAS=3
else
    RESOURCE_GROUP="${AZURE_RESOURCE_GROUP_DEV}"
    MIN_REPLICAS=1
    MAX_REPLICAS=2
fi

# Check if logged in to Azure
echo -e "${YELLOW}Checking Azure login...${NC}"
if ! az account show > /dev/null 2>&1; then
    echo -e "${RED}Not logged in to Azure. Please run: az login${NC}"
    exit 1
fi

# Get current subscription
SUBSCRIPTION=$(az account show --query name -o tsv)
echo -e "${GREEN}Using subscription: $SUBSCRIPTION${NC}"
echo ""

# Build full container image name
CONTAINER_IMAGE="ghcr.io/${GITHUB_REPOSITORY:-rush-university/policy-rag}/backend:${IMAGE_TAG}"

echo -e "${YELLOW}Deploying backend...${NC}"
echo -e "Resource Group: $RESOURCE_GROUP"
echo -e "Container Image: $CONTAINER_IMAGE"
echo ""

# Deploy using Bicep template
az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$PROJECT_ROOT/infrastructure/azure-container-app.bicep" \
    --parameters \
        environment="$ENVIRONMENT" \
        containerImage="$CONTAINER_IMAGE" \
        registryPassword="$GITHUB_TOKEN" \
        searchEndpoint="$SEARCH_ENDPOINT" \
        searchApiKey="$SEARCH_API_KEY" \
        aoaiEndpoint="$AOAI_ENDPOINT" \
        aoaiApiKey="$AOAI_API" \
        storageConnectionString="$STORAGE_CONNECTION_STRING" \
        appInsightsConnectionString="$APPLICATIONINSIGHTS_CONNECTION_STRING" \
        minReplicas="$MIN_REPLICAS" \
        maxReplicas="$MAX_REPLICAS" \
    --query properties.outputs.fqdn.value \
    -o tsv

# Get the FQDN
FQDN=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name azure-container-app \
    --query properties.outputs.fqdn.value \
    -o tsv)

echo ""
echo -e "${GREEN}==============================================================================${NC}"
echo -e "${GREEN}Deployment successful!${NC}"
echo -e "${GREEN}==============================================================================${NC}"
echo -e "Backend URL: ${YELLOW}https://$FQDN${NC}"
echo ""

# Health check
echo -e "${YELLOW}Running health check...${NC}"
for i in {1..30}; do
    if curl -f -s "https://$FQDN/health" > /dev/null; then
        echo -e "${GREEN}Health check passed!${NC}"
        curl -s "https://$FQDN/health" | jq .
        exit 0
    fi
    echo -e "Attempt $i/30 failed, waiting 10s..."
    sleep 10
done

echo -e "${RED}Health check failed after 5 minutes${NC}"
exit 1
