#!/bin/bash

# =============================================================================
# RUSH Policy RAG - Production Setup Script
# =============================================================================
# This script sets up the complete Azure infrastructure for the RUSH Policy RAG system
#
# Prerequisites:
# - Azure CLI installed and logged in
# - Azure subscription with appropriate permissions
# - GitHub CLI installed (for secrets management)
#
# Usage:
#   ./setup-production.sh --env production
#   ./setup-production.sh --env staging --serverless
#   ./setup-production.sh --env production --full-stack

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
ENVIRONMENT="production"
DEPLOYMENT_TYPE="serverless"  # serverless or full-stack
LOCATION="eastus"
PROJECT_NAME="rush-policy"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --env)
      ENVIRONMENT="$2"
      shift 2
      ;;
    --serverless)
      DEPLOYMENT_TYPE="serverless"
      shift
      ;;
    --full-stack)
      DEPLOYMENT_TYPE="full-stack"
      shift
      ;;
    --location)
      LOCATION="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --env <environment>       Environment: production, staging, dev (default: production)"
      echo "  --serverless              Deploy serverless architecture (default)"
      echo "  --full-stack              Deploy full stack with backend"
      echo "  --location <location>     Azure region (default: eastus)"
      echo "  -h, --help                Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Resource names
RESOURCE_GROUP="${PROJECT_NAME}-rg-${ENVIRONMENT}"
SEARCH_SERVICE="${PROJECT_NAME}-search-${ENVIRONMENT}"
STORAGE_ACCOUNT="${PROJECT_NAME}storage${ENVIRONMENT}"
KEY_VAULT="${PROJECT_NAME}-kv-${ENVIRONMENT}"
APP_INSIGHTS="${PROJECT_NAME}-insights-${ENVIRONMENT}"

echo -e "${BLUE}==============================================================================${NC}"
echo -e "${BLUE}RUSH Policy RAG - Production Setup${NC}"
echo -e "${BLUE}==============================================================================${NC}"
echo -e "Environment: ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Deployment Type: ${YELLOW}${DEPLOYMENT_TYPE}${NC}"
echo -e "Location: ${YELLOW}${LOCATION}${NC}"
echo -e "Resource Group: ${YELLOW}${RESOURCE_GROUP}${NC}"
echo ""

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v az &> /dev/null; then
    echo -e "${RED}Azure CLI not found. Please install: https://aka.ms/azure-cli${NC}"
    exit 1
fi

if ! az account show &> /dev/null; then
    echo -e "${RED}Not logged in to Azure. Please run: az login${NC}"
    exit 1
fi

if ! command -v gh &> /dev/null; then
    echo -e "${YELLOW}GitHub CLI not found. Install for automatic secrets management: https://cli.github.com${NC}"
fi

SUBSCRIPTION=$(az account show --query name -o tsv)
echo -e "${GREEN}Using Azure subscription: ${SUBSCRIPTION}${NC}"
echo ""

# =============================================================================
# 1. CREATE RESOURCE GROUP
# =============================================================================
echo -e "${BLUE}Step 1: Creating Resource Group${NC}"

if az group show --name "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${YELLOW}Resource group already exists${NC}"
else
    az group create \
        --name "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --tags Environment="$ENVIRONMENT" Project="$PROJECT_NAME"
    echo -e "${GREEN}Resource group created${NC}"
fi

# =============================================================================
# 2. CREATE AZURE AI SEARCH
# =============================================================================
echo -e "${BLUE}Step 2: Creating Azure AI Search${NC}"

if az search service show --name "$SEARCH_SERVICE" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${YELLOW}Search service already exists${NC}"
else
    az search service create \
        --name "$SEARCH_SERVICE" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku standard \
        --partition-count 1 \
        --replica-count 1

    echo -e "${GREEN}Search service created${NC}"
fi

# Get Search credentials
SEARCH_ENDPOINT="https://${SEARCH_SERVICE}.search.windows.net"
SEARCH_API_KEY=$(az search admin-key show \
    --service-name "$SEARCH_SERVICE" \
    --resource-group "$RESOURCE_GROUP" \
    --query primaryKey -o tsv)

echo -e "${GREEN}Search Endpoint: ${SEARCH_ENDPOINT}${NC}"

# =============================================================================
# 3. CREATE STORAGE ACCOUNT
# =============================================================================
echo -e "${BLUE}Step 3: Creating Storage Account${NC}"

if az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${YELLOW}Storage account already exists${NC}"
else
    az storage account create \
        --name "$STORAGE_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku Standard_LRS \
        --encryption-services blob \
        --https-only true

    echo -e "${GREEN}Storage account created${NC}"
fi

# Get Storage connection string
STORAGE_CONNECTION_STRING=$(az storage account show-connection-string \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --query connectionString -o tsv)

# Create policies container
az storage container create \
    --name "policies-active" \
    --account-name "$STORAGE_ACCOUNT" \
    --connection-string "$STORAGE_CONNECTION_STRING" \
    --public-access off

echo -e "${GREEN}Storage container created${NC}"

# =============================================================================
# 4. CREATE KEY VAULT
# =============================================================================
echo -e "${BLUE}Step 4: Creating Key Vault${NC}"

if az keyvault show --name "$KEY_VAULT" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${YELLOW}Key Vault already exists${NC}"
else
    az keyvault create \
        --name "$KEY_VAULT" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --enable-soft-delete true \
        --enable-purge-protection false

    echo -e "${GREEN}Key Vault created${NC}"
fi

# Store secrets in Key Vault
echo -e "${YELLOW}Storing secrets in Key Vault...${NC}"

az keyvault secret set \
    --vault-name "$KEY_VAULT" \
    --name "search-endpoint" \
    --value "$SEARCH_ENDPOINT" > /dev/null

az keyvault secret set \
    --vault-name "$KEY_VAULT" \
    --name "search-api-key" \
    --value "$SEARCH_API_KEY" > /dev/null

az keyvault secret set \
    --vault-name "$KEY_VAULT" \
    --name "storage-connection-string" \
    --value "$STORAGE_CONNECTION_STRING" > /dev/null

echo -e "${GREEN}Secrets stored in Key Vault${NC}"

# =============================================================================
# 5. CREATE APPLICATION INSIGHTS
# =============================================================================
echo -e "${BLUE}Step 5: Creating Application Insights${NC}"

if az monitor app-insights component show \
    --app "$APP_INSIGHTS" \
    --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${YELLOW}Application Insights already exists${NC}"
else
    az monitor app-insights component create \
        --app "$APP_INSIGHTS" \
        --location "$LOCATION" \
        --resource-group "$RESOURCE_GROUP" \
        --application-type web

    echo -e "${GREEN}Application Insights created${NC}"
fi

APP_INSIGHTS_CONNECTION_STRING=$(az monitor app-insights component show \
    --app "$APP_INSIGHTS" \
    --resource-group "$RESOURCE_GROUP" \
    --query connectionString -o tsv)

# =============================================================================
# 6. SETUP KNOWLEDGE AGENT
# =============================================================================
echo -e "${BLUE}Step 6: Setting up Knowledge Agent${NC}"

echo -e "${YELLOW}Please ensure you have:${NC}"
echo -e "  1. Created the 'rush-policies' search index"
echo -e "  2. Configured Azure OpenAI with gpt-4o-mini deployment"
echo -e ""
echo -e "Run the following command to create the Knowledge Agent:"
echo -e "${GREEN}cd apps/backend && python setup_knowledge_agent.py --create-all${NC}"
echo -e ""

# =============================================================================
# 7. FULL STACK: CREATE CONTAINER APPS
# =============================================================================
if [ "$DEPLOYMENT_TYPE" = "full-stack" ]; then
    echo -e "${BLUE}Step 7: Creating Container Apps Environment${NC}"

    CONTAINER_ENV="${PROJECT_NAME}-env-${ENVIRONMENT}"
    CONTAINER_APP="${PROJECT_NAME}-backend-${ENVIRONMENT}"

    if az containerapp env show \
        --name "$CONTAINER_ENV" \
        --resource-group "$RESOURCE_GROUP" &> /dev/null; then
        echo -e "${YELLOW}Container Apps Environment already exists${NC}"
    else
        az containerapp env create \
            --name "$CONTAINER_ENV" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$LOCATION" \
            --logs-destination azure-monitor \
            --logs-workspace-id "$(az monitor app-insights component show \
                --app "$APP_INSIGHTS" \
                --resource-group "$RESOURCE_GROUP" \
                --query 'customerId' -o tsv)"

        echo -e "${GREEN}Container Apps Environment created${NC}"
    fi
fi

# =============================================================================
# 8. CREATE SERVICE PRINCIPAL FOR GITHUB ACTIONS
# =============================================================================
echo -e "${BLUE}Step 8: Creating Service Principal for GitHub Actions${NC}"

SP_NAME="github-actions-${PROJECT_NAME}-${ENVIRONMENT}"

# Check if SP already exists
EXISTING_SP=$(az ad sp list --display-name "$SP_NAME" --query "[0].appId" -o tsv)

if [ -n "$EXISTING_SP" ]; then
    echo -e "${YELLOW}Service Principal already exists${NC}"
    APP_ID="$EXISTING_SP"
else
    # Get subscription ID and resource group ID
    SUBSCRIPTION_ID=$(az account show --query id -o tsv)
    RG_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"

    # Create service principal
    SP_CREDENTIALS=$(az ad sp create-for-rbac \
        --name "$SP_NAME" \
        --role contributor \
        --scopes "$RG_ID" \
        --sdk-auth)

    echo -e "${GREEN}Service Principal created${NC}"

    # Extract credentials
    APP_ID=$(echo "$SP_CREDENTIALS" | jq -r .clientId)

    echo -e "${YELLOW}Save these credentials as GitHub secret:${NC}"
    echo -e "Secret name: ${GREEN}AZURE_CREDENTIALS_${ENVIRONMENT^^}${NC}"
    echo "$SP_CREDENTIALS"
fi

# Grant Key Vault access to Service Principal
az keyvault set-policy \
    --name "$KEY_VAULT" \
    --object-id "$(az ad sp show --id "$APP_ID" --query id -o tsv)" \
    --secret-permissions get list > /dev/null

echo -e "${GREEN}Key Vault access granted to Service Principal${NC}"

# =============================================================================
# 9. CONFIGURE GITHUB SECRETS (if gh CLI available)
# =============================================================================
if command -v gh &> /dev/null; then
    echo -e "${BLUE}Step 9: Configuring GitHub Secrets${NC}"

    # Check if we're in a git repo
    if git rev-parse --git-dir > /dev/null 2>&1; then
        ENV_UPPER=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')

        echo -e "${YELLOW}Setting GitHub secrets for ${ENVIRONMENT}...${NC}"

        gh secret set "SEARCH_ENDPOINT_${ENV_UPPER}" --body "$SEARCH_ENDPOINT" || true
        gh secret set "SEARCH_API_KEY_${ENV_UPPER}" --body "$SEARCH_API_KEY" || true
        gh secret set "AZURE_RESOURCE_GROUP_${ENV_UPPER}" --body "$RESOURCE_GROUP" || true

        echo -e "${GREEN}GitHub secrets configured${NC}"
    else
        echo -e "${YELLOW}Not in a git repository. Skipping GitHub secrets setup${NC}"
    fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo -e "${BLUE}==============================================================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${BLUE}==============================================================================${NC}"
echo ""
echo -e "${YELLOW}Resources Created:${NC}"
echo -e "  Resource Group: ${GREEN}${RESOURCE_GROUP}${NC}"
echo -e "  Search Service: ${GREEN}${SEARCH_SERVICE}${NC}"
echo -e "  Search Endpoint: ${GREEN}${SEARCH_ENDPOINT}${NC}"
echo -e "  Storage Account: ${GREEN}${STORAGE_ACCOUNT}${NC}"
echo -e "  Key Vault: ${GREEN}${KEY_VAULT}${NC}"
echo -e "  App Insights: ${GREEN}${APP_INSIGHTS}${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo ""

if [ "$DEPLOYMENT_TYPE" = "serverless" ]; then
    echo -e "1. Setup Knowledge Agent:"
    echo -e "   ${GREEN}cd apps/backend${NC}"
    echo -e "   ${GREEN}python setup_knowledge_agent.py --create-all${NC}"
    echo ""
    echo -e "2. Configure Vercel environment variables:"
    echo -e "   ${GREEN}NEXT_PUBLIC_SEARCH_ENDPOINT=${SEARCH_ENDPOINT}${NC}"
    echo -e "   ${GREEN}NEXT_PUBLIC_SEARCH_API_KEY=<from-key-vault>${NC}"
    echo -e "   ${GREEN}NEXT_PUBLIC_AGENT_NAME=rush-policy-agent${NC}"
    echo ""
    echo -e "3. Deploy to Vercel:"
    echo -e "   ${GREEN}cd apps/frontend${NC}"
    echo -e "   ${GREEN}vercel --prod${NC}"
else
    echo -e "1. Setup Knowledge Agent (same as above)"
    echo ""
    echo -e "2. Configure GitHub secrets and deploy via GitHub Actions"
    echo ""
    echo -e "3. Or deploy manually with:"
    echo -e "   ${GREEN}./scripts/deploy-to-azure.sh ${ENVIRONMENT} latest${NC}"
fi

echo ""
echo -e "${YELLOW}Key Vault Secrets:${NC}"
echo -e "  View secrets: ${GREEN}az keyvault secret list --vault-name ${KEY_VAULT}${NC}"
echo -e "  Get a secret: ${GREEN}az keyvault secret show --vault-name ${KEY_VAULT} --name <secret-name>${NC}"
echo ""
