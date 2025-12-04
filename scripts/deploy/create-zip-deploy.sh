#!/usr/bin/env bash
# Create a proper ZIP deployment package for Azure Web App Service
# This script validates the package structure and creates an optimized ZIP file
# Usage: create-zip-deploy.sh [output-file]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/apps/backend"
OUTPUT_FILE="${1:-$BACKEND_DIR/deploy.zip}"

echo "================================================================================"
echo "Creating ZIP Deployment Package for Azure Web App Service"
echo "================================================================================"
echo "Backend Directory: $BACKEND_DIR"
echo "Output File: $OUTPUT_FILE"
echo ""

# Check if backend directory exists
if [[ ! -d "$BACKEND_DIR" ]]; then
  echo "Error: Backend directory not found: $BACKEND_DIR" >&2
  exit 1
fi

cd "$BACKEND_DIR"

# Validation checks
echo "Step 1: Validating deployment requirements..."
ERRORS=0

# Check for main.py
if [[ ! -f "main.py" ]]; then
  echo "❌ ERROR: main.py not found in $BACKEND_DIR" >&2
  ERRORS=$((ERRORS + 1))
else
  echo "✓ main.py found"
fi

# Check for requirements.txt
if [[ ! -f "requirements.txt" ]]; then
  echo "❌ ERROR: requirements.txt not found in $BACKEND_DIR" >&2
  ERRORS=$((ERRORS + 1))
else
  echo "✓ requirements.txt found"
  
  # Check for critical dependencies
  if ! grep -q "fastapi" requirements.txt; then
    echo "⚠ WARNING: fastapi not found in requirements.txt" >&2
  fi
  if ! grep -q "uvicorn" requirements.txt; then
    echo "⚠ WARNING: uvicorn not found in requirements.txt" >&2
  fi
  if ! grep -q "gunicorn" requirements.txt; then
    echo "⚠ WARNING: gunicorn not found in requirements.txt (needed for production)" >&2
  fi
fi

# Check for app directory
if [[ ! -d "app" ]]; then
  echo "❌ ERROR: app/ directory not found" >&2
  ERRORS=$((ERRORS + 1))
else
  echo "✓ app/ directory found"
fi

# Check file sizes (warn about large files)
echo ""
echo "Step 2: Checking for large files that might cause issues..."
LARGE_FILES=$(find . -type f -size +10M ! -path "./.git/*" ! -path "./venv/*" ! -path "./.venv/*" ! -path "./__pycache__/*" 2>/dev/null | head -10)
if [[ -n "$LARGE_FILES" ]]; then
  echo "⚠ WARNING: Large files found (>10MB):"
  echo "$LARGE_FILES" | while read -r file; do
    SIZE=$(du -h "$file" | cut -f1)
    echo "  - $file ($SIZE)"
  done
  echo "  Consider excluding these from deployment"
else
  echo "✓ No unusually large files found"
fi

if [[ $ERRORS -gt 0 ]]; then
  echo ""
  echo "❌ Validation failed with $ERRORS error(s). Please fix these issues before deploying." >&2
  exit 1
fi

# Remove existing ZIP if it exists
if [[ -f "$OUTPUT_FILE" ]]; then
  echo ""
  echo "Step 3: Removing existing deployment package..."
  rm -f "$OUTPUT_FILE"
  echo "✓ Removed existing $OUTPUT_FILE"
fi

echo ""
echo "Step 4: Creating ZIP deployment package..."

# Create ZIP with proper exclusions
# Exclude:
# - Python cache files
# - Virtual environments
# - Environment files
# - Test files and data
# - Git files
# - IDE files
# - Large data files
# - Docker files (not needed for ZIP deploy)
zip -r "$OUTPUT_FILE" . \
  -x "*.pyc" \
  -x "*.pyo" \
  -x "*__pycache__/*" \
  -x "*.pytest_cache/*" \
  -x ".env" \
  -x ".env.*" \
  -x "venv/*" \
  -x ".venv/*" \
  -x "env/*" \
  -x ".git/*" \
  -x ".gitignore" \
  -x ".vscode/*" \
  -x ".idea/*" \
  -x "*.swp" \
  -x "*.swo" \
  -x "*~" \
  -x "tests/*" \
  -x "test_*.py" \
  -x "*_test.py" \
  -x "data/test_pdfs/*" \
  -x "data/*.json" \
  -x "evaluation/*" \
  -x "Dockerfile" \
  -x "docker-compose.yml" \
  -x ".dockerignore" \
  -x "README.md" \
  -x "pytest.ini" \
  -x "requirements-test.txt" \
  -x "*.log" \
  -x "*.tmp" \
  -x ".DS_Store" \
  -x "Thumbs.db" \
  > /dev/null 2>&1

if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "❌ ERROR: Failed to create ZIP file" >&2
  exit 1
fi

ZIP_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
echo "✓ ZIP package created: $OUTPUT_FILE ($ZIP_SIZE)"

# Verify ZIP contents
echo ""
echo "Step 5: Verifying ZIP package contents..."
ZIP_CONTENTS=$(unzip -l "$OUTPUT_FILE" 2>/dev/null | grep -E "(main\.py|requirements\.txt|app/)" | head -5 || true)

if [[ -z "$ZIP_CONTENTS" ]]; then
  echo "⚠ WARNING: Could not verify ZIP contents"
else
  echo "✓ ZIP contains required files:"
  echo "$ZIP_CONTENTS" | head -3 | sed 's/^/  /'
fi

# Check ZIP size
ZIP_SIZE_BYTES=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null)
MAX_SIZE=$((100 * 1024 * 1024))  # 100MB

if [[ $ZIP_SIZE_BYTES -gt $MAX_SIZE ]]; then
  echo ""
  echo "⚠ WARNING: ZIP file is large ($ZIP_SIZE). Azure has a 100MB limit for ZIP deploy."
  echo "  Consider excluding more files or using container deployment instead."
fi

echo ""
echo "================================================================================"
echo "ZIP Package Created Successfully!"
echo "================================================================================"
echo ""
echo "Package: $OUTPUT_FILE"
echo "Size: $ZIP_SIZE"
echo ""
echo "Next steps:"
echo "1. Fix Azure Web App configuration (if not done already):"
echo "   ./scripts/deploy/fix-zip-deploy.sh <resource-group> <app-name>"
echo ""
echo "2. Deploy the ZIP file:"
echo "   az webapp deployment source config-zip \\"
echo "     --name <app-name> \\"
echo "     --resource-group <resource-group> \\"
echo "     --src $OUTPUT_FILE"
echo ""
echo "3. Monitor deployment:"
echo "   az webapp log tail --name <app-name> --resource-group <resource-group>"
echo ""
echo "4. If deployment fails, check logs and consider:"
echo "   - Switching to Azure Web App for Containers (Docker)"
echo "   - Using Azure Container Apps (recommended for FastAPI)"
echo ""



