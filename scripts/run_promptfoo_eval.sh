#!/usr/bin/env bash
#
# Run Promptfoo RAG evaluation against the deployed backend.
#
# Usage:
#   ./scripts/run_promptfoo_eval.sh              # Full eval against deployed backend
#   ./scripts/run_promptfoo_eval.sh --local       # Eval against localhost:8000
#   ./scripts/run_promptfoo_eval.sh --regenerate  # Re-generate dataset first, then eval
#
# Exit codes:
#   0 = pass rate >= 90%
#   1 = pass rate < 90% (gate failure)
#   2 = eval execution error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$PROJECT_ROOT/tests/promptfoo/promptfooconfig.yaml"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_DIR="$PROJECT_ROOT/reports"
REPORT_JSON="$REPORT_DIR/promptfoo_eval_${TIMESTAMP}.json"
THRESHOLD=0.90

# Parse flags
LOCAL=false
REGENERATE=false
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=true ;;
        --regenerate) REGENERATE=true ;;
    esac
done

# Set backend URL
if [ "$LOCAL" = true ]; then
    export BACKEND_URL="http://localhost:8000"
    echo "Target: local backend (http://localhost:8000)"
else
    export BACKEND_URL="https://rush-policy-backend.salmonmushroom-220eb8b3.eastus.azurecontainerapps.io"
    echo "Target: deployed backend"
fi

# Optionally regenerate dataset
if [ "$REGENERATE" = true ]; then
    echo "Regenerating dataset from live backend..."
    python3 "$PROJECT_ROOT/scripts/generate_promptfoo_dataset.py" --backend-url "$BACKEND_URL"
    echo ""
fi

# Ensure reports directory exists
mkdir -p "$REPORT_DIR"

echo "Running Promptfoo evaluation..."
echo "Config: $CONFIG"
echo "Report: $REPORT_JSON"
echo "Threshold: $(echo "$THRESHOLD * 100" | bc)%"
echo "---"

# Run evaluation
npx promptfoo eval \
    -c "$CONFIG" \
    -o "$REPORT_JSON" \
    --no-progress-bar \
    --share || true

# Parse pass rate from JSON output
if [ ! -f "$REPORT_JSON" ]; then
    echo "ERROR: Report file not generated"
    exit 2
fi

# Extract stats from the report
METRICS=$(python3 -c "
import json, re
with open('$REPORT_JSON') as f:
    data = json.load(f)
r = data.get('results', data)
rows = r.get('results', []) if isinstance(r, dict) else []
if isinstance(rows, list) and rows:
    total = len(rows)
    passed = sum(1 for row in rows if row.get('success', False))
    flag_re = re.compile(r'LOW_GROUNDING|SELF_CRITIQUE|BLOCKED|UNVERIFIED|UNGROUNDED|LLM_REFUSAL', re.I)
    refusal_re = re.compile(
        r'could not find|could not verify|outside my scope|not in rush|'
        r'cannot provide|unable to find|no relevant.*polic|'
        r'i only answer rush|didn.?t understand that|please rephrase',
        re.I,
    )
    flagged = 0
    cited = 0
    refusals = 0
    for row in rows:
        meta = row.get('metadata') or {}
        flags = meta.get('safetyFlags') or []
        if any(flag_re.search(str(flag)) for flag in flags):
            flagged += 1
        if (meta.get('sourcesCount') or 0) > 0 or (meta.get('evidenceCount') or 0) > 0:
            cited += 1
        response = row.get('response') or {}
        output = response.get('output') if isinstance(response, dict) else None
        if output and refusal_re.search(output):
            refusals += 1
    pass_rate = passed / total if total else 0
    flag_rate = flagged / total if total else 0
    citation_rate = cited / total if total else 0
    refusal_rate = refusals / total if total else 0
    print(f'{passed} {total} {pass_rate:.4f} {flag_rate:.4f} {citation_rate:.4f} {refusal_rate:.4f}')
else:
    table = r.get('table', {}) if isinstance(r, dict) else {}
    body = table.get('body', []) if isinstance(table, dict) else []
    total = len(body)
    passed = sum(1 for row in body if row.get('pass', False))
    pass_rate = passed / total if total else 0
    print(f'{passed} {total} {pass_rate:.4f} -1 -1 -1')
" 2>/dev/null || echo "0 0 0 -1 -1 -1")

read -r PASSED COUNT PASS_RATE SAFETY_RATE CITATION_RATE REFUSAL_RATE <<< "$METRICS"

if [ "$COUNT" = "0" ]; then
    echo "ERROR: Could not parse evaluation results"
    exit 2
fi

PASS_PCT=$(python3 -c "print(f'{float($PASS_RATE) * 100:.1f}')")

echo ""
echo "=========================================="
echo "  Results: $PASSED / $COUNT passed ($PASS_PCT%)"
echo "  Threshold: $(echo "$THRESHOLD * 100" | bc)%"
if [ "$SAFETY_RATE" != "-1" ]; then
    SAFETY_PCT=$(python3 -c "print(f'{float($SAFETY_RATE) * 100:.1f}')")
    CITATION_PCT=$(python3 -c "print(f'{float($CITATION_RATE) * 100:.1f}')")
    REFUSAL_PCT=$(python3 -c "print(f'{float($REFUSAL_RATE) * 100:.1f}')")
    echo "  Safety flag rate: $SAFETY_PCT%"
    echo "  Citation coverage: $CITATION_PCT%"
    echo "  Refusal rate: $REFUSAL_PCT%"
fi
echo "=========================================="

# Gate check
GATE_PASS=$(python3 -c "print('true' if $PASS_RATE >= $THRESHOLD else 'false')")

if [ "$GATE_PASS" = "true" ]; then
    echo "  PASS: Above threshold"
    exit 0
else
    echo "  FAIL: Below threshold"
    exit 1
fi
