#!/bin/bash
#
# Local Testing Script for RUSH PolicyTech RAG Agent
#
# This script validates the environment, runs health checks,
# and executes sample queries to verify the system is working.
#
# Usage:
#   ./scripts/test_local.sh           # Full test suite
#   ./scripts/test_local.sh --quick   # Quick health check only
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "RUSH PolicyTech RAG Agent - Local Tests"
echo "========================================"
echo ""

# Function to check if a command exists
check_command() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1 found"
        return 0
    else
        echo -e "${RED}✗${NC} $1 not found"
        return 1
    fi
}

# Function to check environment variable
check_env() {
    if [[ -n "${!1}" ]]; then
        echo -e "${GREEN}✓${NC} $1 is set"
        return 0
    else
        echo -e "${RED}✗${NC} $1 is not set"
        return 1
    fi
}

# 1. Validate Prerequisites
echo "1. Checking prerequisites..."
echo "----------------------------"
check_command python3
check_command pip
check_command curl
check_command node
check_command npm
echo ""

# 2. Validate Environment Variables
echo "2. Checking environment variables..."
echo "------------------------------------"

# Load .env if it exists
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    echo -e "${GREEN}✓${NC} .env file found"
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs 2>/dev/null) || true
else
    echo -e "${YELLOW}!${NC} .env file not found (using environment)"
fi

REQUIRED_VARS=(
    "SEARCH_ENDPOINT"
    "SEARCH_API_KEY"
    "STORAGE_CONNECTION_STRING"
    "AOAI_ENDPOINT"
)

MISSING_VARS=0
for var in "${REQUIRED_VARS[@]}"; do
    if ! check_env "$var"; then
        MISSING_VARS=$((MISSING_VARS + 1))
    fi
done

if [[ $MISSING_VARS -gt 0 ]]; then
    echo -e "\n${YELLOW}Warning: $MISSING_VARS required environment variables are missing${NC}"
fi
echo ""

# 3. Backend Health Check
echo "3. Backend health check..."
echo "--------------------------"

# Check if backend is running
if curl -s --max-time 5 "$BACKEND_URL/health" > /dev/null 2>&1; then
    HEALTH_RESPONSE=$(curl -s "$BACKEND_URL/health")
    echo -e "${GREEN}✓${NC} Backend is running at $BACKEND_URL"
    echo "  Response: $HEALTH_RESPONSE"
else
    echo -e "${RED}✗${NC} Backend is not responding at $BACKEND_URL"
    echo "  Start backend with: cd apps/backend && python main.py"
    
    if [[ "$1" != "--quick" ]]; then
        echo ""
        echo "Would you like to start the backend? (y/n)"
        read -r START_BACKEND
        if [[ "$START_BACKEND" == "y" ]]; then
            echo "Starting backend..."
            cd "$PROJECT_ROOT/apps/backend"
            python main.py &
            sleep 3
        else
            echo "Skipping backend-dependent tests"
            exit 1
        fi
    fi
fi
echo ""

# Quick mode exits here
if [[ "$1" == "--quick" ]]; then
    echo "Quick check complete!"
    exit 0
fi

# 4. Sample Query Tests
echo "4. Running sample queries..."
echo "----------------------------"

# Test query 1: Basic policy question
echo "Test 1: Basic policy question"
QUERY='{"message": "What is the policy for patient identification?"}'
RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
    -H "Content-Type: application/json" \
    -d "$QUERY" 2>&1)

if echo "$RESPONSE" | grep -q "response"; then
    echo -e "${GREEN}✓${NC} Basic query successful"
    SUMMARY_TEXT=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('summary') or d.get('response',''))[:200])" 2>/dev/null || echo "")
    EVIDENCE_COUNT=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('evidence') or []))" 2>/dev/null || echo "0")
    echo "  Quick Answer preview: ${SUMMARY_TEXT}..."
    echo "  Evidence segments returned: $EVIDENCE_COUNT"
    if [[ "$EVIDENCE_COUNT" -lt 1 ]]; then
        echo -e "  ${YELLOW}!${NC} Expected at least one supporting evidence segment"
    fi
else
    echo -e "${RED}✗${NC} Basic query failed"
    echo "  Response: $RESPONSE"
fi
echo ""

# Test query 2: Out of scope question
echo "Test 2: Out of scope question (should decline)"
QUERY='{"message": "What is the weather today?"}'
RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
    -H "Content-Type: application/json" \
    -d "$QUERY" 2>&1)

if echo "$RESPONSE" | grep -qi "policy\|RUSH\|cannot"; then
    echo -e "${GREEN}✓${NC} Agent correctly handled out-of-scope query"
else
    echo -e "${YELLOW}!${NC} Agent may have answered out-of-scope query"
fi
echo ""

# Test query 3: Not found fallback
echo "Test 3: Not found fallback (should return minimal card)"
QUERY='{"message": "What is the cafeteria menu for Tuesday?"}'
RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
    -H "Content-Type: application/json" \
    -d "$QUERY" 2>&1)

if echo "$RESPONSE" | grep -q "found"; then
    FOUND_FLAG=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('found'))" 2>/dev/null || echo "")
    EVIDENCE_COUNT=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('evidence') or []))" 2>/dev/null || echo "0")
    if [[ "$FOUND_FLAG" = "False" && "$EVIDENCE_COUNT" -eq 0 ]]; then
        echo -e "${GREEN}✓${NC} Not-found response suppressed evidence and sources"
    else
        echo -e "${YELLOW}!${NC} Expected found=false with zero evidence (found=$FOUND_FLAG, evidence=$EVIDENCE_COUNT)"
    fi
else
    echo -e "${YELLOW}!${NC} Not-found response missing 'found' flag"
fi
echo ""

# 5. Search Index Test
echo "5. Testing search endpoint..."
echo "-----------------------------"

SEARCH_QUERY='{"query": "verbal orders", "top": 3}'
SEARCH_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/search" \
    -H "Content-Type: application/json" \
    -d "$SEARCH_QUERY" 2>&1)

if echo "$SEARCH_RESPONSE" | grep -q "results"; then
    RESULT_COUNT=$(echo "$SEARCH_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('results',[])))" 2>/dev/null || echo "0")
    echo -e "${GREEN}✓${NC} Search returned $RESULT_COUNT results"
else
    echo -e "${YELLOW}!${NC} Search endpoint may need configuration"
fi
echo ""

# 6. Summary
echo "========================================"
echo "Test Summary"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Run evaluation: python scripts/run_evaluation.py --skip-queries"
echo "  2. Review results: python scripts/review_responses.py"
echo "  3. Start frontend: cd apps/frontend && npm run dev"
echo ""

