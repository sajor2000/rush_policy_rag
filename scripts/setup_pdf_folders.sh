#!/bin/bash
# Create all required directories for PDF indexing workflow

# Get script directory (works on any system)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Validate repo root exists
if [ ! -d "$REPO_ROOT" ]; then
    echo "❌ Error: Repository root not found: $REPO_ROOT"
    exit 1
fi

echo "Setting up PDF staging directories in: $REPO_ROOT"
echo ""

# Create directories with error checking
for dir in \
    "$REPO_ROOT/pdf_staging/01_new_pdfs" \
    "$REPO_ROOT/pdf_staging/02_test_batch" \
    "$REPO_ROOT/pdf_staging/03_validated" \
    "$REPO_ROOT/pdf_staging/04_audit_reports" \
    "$REPO_ROOT/apps/backend/data/test_pdfs"; do

    if ! mkdir -p "$dir"; then
        echo "❌ Failed to create directory: $dir"
        exit 1
    fi
done

echo "✅ Folder structure created:"
echo "  - $REPO_ROOT/pdf_staging/01_new_pdfs (place your PDFs here)"
echo "  - $REPO_ROOT/pdf_staging/02_test_batch (test batch of 30)"
echo "  - $REPO_ROOT/pdf_staging/03_validated (passed validation)"
echo "  - $REPO_ROOT/pdf_staging/04_audit_reports (quality reports)"
echo ""
echo "Next steps:"
echo "  1. Copy your PDFs to: pdf_staging/01_new_pdfs/"
echo "  2. Run hardware detection: python scripts/detect_mac_hardware.py"
echo "  3. See full guide: PDF_INDEXING_GUIDE.md"
