#!/bin/bash
# Local Validation Script for RUSH Policy RAG
# Run this before deploying to dev environment
#
# Usage:
#   chmod +x scripts/local_validation.sh
#   ./scripts/local_validation.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================================"
echo "RUSH Policy RAG - Local Validation Suite"
echo "============================================================"
echo ""

# Track failures
FAILURES=0

# Function to run a check
run_check() {
    local name="$1"
    local cmd="$2"

    echo -e "${YELLOW}=== $name ===${NC}"
    if eval "$cmd"; then
        echo -e "${GREEN}[PASS]${NC} $name"
        echo ""
        return 0
    else
        echo -e "${RED}[FAIL]${NC} $name"
        echo ""
        FAILURES=$((FAILURES + 1))
        return 1
    fi
}

# 1. Check Backend Health
echo -e "${YELLOW}=== 1. Backend Health Check ===${NC}"
if curl -s http://localhost:8000/health | grep -q '"status"'; then
    HEALTH_STATUS=$(curl -s http://localhost:8000/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status', 'unknown'))")
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        echo -e "${GREEN}[PASS]${NC} Backend is healthy"
    else
        echo -e "${YELLOW}[WARN]${NC} Backend status: $HEALTH_STATUS"
    fi
else
    echo -e "${RED}[FAIL]${NC} Backend not responding. Start with: ./start_backend.sh"
    echo "Continuing with other checks..."
fi
echo ""

# 2. Check Python dependencies
echo -e "${YELLOW}=== 2. Dependency Check ===${NC}"
cd "$PROJECT_ROOT/apps/backend"

MISSING_DEPS=""
for pkg in ragas deepeval pymupdf langchain-openai httpx pytest azure-identity; do
    if ! pip show "$pkg" > /dev/null 2>&1; then
        MISSING_DEPS="$MISSING_DEPS $pkg"
    fi
done

if [ -z "$MISSING_DEPS" ]; then
    echo -e "${GREEN}[PASS]${NC} All required packages installed"
else
    echo -e "${RED}[FAIL]${NC} Missing packages:$MISSING_DEPS"
    echo "Install with: pip install$MISSING_DEPS"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# 3. Check environment variables
echo -e "${YELLOW}=== 3. Environment Check ===${NC}"
source "$PROJECT_ROOT/.env" 2>/dev/null || true

MISSING_VARS=""
for var in AOAI_ENDPOINT AOAI_API_KEY SEARCH_ENDPOINT SEARCH_API_KEY; do
    if [ -z "${!var}" ]; then
        MISSING_VARS="$MISSING_VARS $var"
    fi
done

if [ -z "$MISSING_VARS" ]; then
    echo -e "${GREEN}[PASS]${NC} Required environment variables set"
else
    echo -e "${RED}[FAIL]${NC} Missing environment variables:$MISSING_VARS"
    FAILURES=$((FAILURES + 1))
fi

# Check for eval deployment (gpt-4.1-mini recommended)
if [ -n "${AOAI_EVAL_DEPLOYMENT}" ]; then
    echo -e "${GREEN}[PASS]${NC} Eval deployment: $AOAI_EVAL_DEPLOYMENT (faster/cheaper)"
else
    echo -e "${YELLOW}[WARN]${NC} AOAI_EVAL_DEPLOYMENT not set - will use $AOAI_CHAT_DEPLOYMENT"
    echo "       Recommend adding: AOAI_EVAL_DEPLOYMENT=gpt-4.1-mini to .env"
fi
echo ""

# 4. DeepEval Unit Tests
echo -e "${YELLOW}=== 4. DeepEval Unit Tests ===${NC}"
cd "$PROJECT_ROOT/apps/backend"

# Set timeout override (240s per attempt, 600s total, 3 retries)
export DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE=240
export DEEPEVAL_TIMEOUT_SECONDS=600
export DEEPEVAL_MAX_RETRIES=3

if pytest tests/test_rag_evaluation.py -v --tb=short -x 2>/dev/null; then
    echo -e "${GREEN}[PASS]${NC} DeepEval tests passed"
else
    echo -e "${RED}[FAIL]${NC} DeepEval tests failed"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# 5. Synonym Service Tests
echo -e "${YELLOW}=== 5. Synonym Service Tests ===${NC}"
if [ -f "$PROJECT_ROOT/apps/backend/tests/test_synonym_service.py" ]; then
    if pytest tests/test_synonym_service.py -v --tb=short 2>/dev/null; then
        echo -e "${GREEN}[PASS]${NC} Synonym service tests passed"
    else
        echo -e "${YELLOW}[WARN]${NC} Synonym tests failed or skipped"
    fi
else
    echo -e "${YELLOW}[SKIP]${NC} No synonym service tests found"
fi
echo ""

# 6. Diagnostics Module Check
echo -e "${YELLOW}=== 6. Diagnostics Module Check ===${NC}"
cd "$PROJECT_ROOT"
if python3 -c "
import sys
sys.path.insert(0, 'apps/backend')
from app.evaluation.diagnostics import RAGDiagnostics
print('RAGDiagnostics module OK')
" 2>/dev/null; then
    echo -e "${GREEN}[PASS]${NC} Diagnostics module imports successfully"
else
    echo -e "${YELLOW}[WARN]${NC} Diagnostics module import failed"
fi
echo ""

# 7. Weekly Eval Dry Run (if backend is available)
echo -e "${YELLOW}=== 7. Weekly Eval Dry Run ===${NC}"
if curl -s http://localhost:8000/health | grep -q '"status"'; then
    cd "$PROJECT_ROOT"
    if python3 scripts/weekly_eval.py --dry-run --sample 3 2>/dev/null; then
        echo -e "${GREEN}[PASS]${NC} Weekly eval dry run completed"
    else
        echo -e "${YELLOW}[WARN]${NC} Weekly eval dry run had issues"
    fi
else
    echo -e "${YELLOW}[SKIP]${NC} Backend not running, skipping weekly eval"
fi
echo ""

# 8. Test Dataset Generator Check
echo -e "${YELLOW}=== 8. Test Dataset Generator Check ===${NC}"
cd "$PROJECT_ROOT"
if python3 -c "
import sys
sys.path.insert(0, 'scripts')
from generate_test_dataset_from_pdfs import PDFParser, TestCase
print('TestsetGenerator modules OK')
"; then
    echo -e "${GREEN}[PASS]${NC} Dataset generator imports successfully"
else
    echo -e "${RED}[FAIL]${NC} Dataset generator import failed"
    FAILURES=$((FAILURES + 1))
fi
echo ""

# Summary
echo "============================================================"
echo "Validation Summary"
echo "============================================================"
if [ $FAILURES -eq 0 ]; then
    echo -e "${GREEN}ALL CHECKS PASSED${NC}"
    echo ""
    echo "Ready for dev deployment!"
    exit 0
else
    echo -e "${RED}$FAILURES CHECKS FAILED${NC}"
    echo ""
    echo "Please fix the issues above before deploying."
    exit 1
fi
