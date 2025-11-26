#!/bin/bash
# Start script for the Next.js frontend

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$REPO_ROOT/apps/frontend"

echo "Starting RUSH Policy RAG Frontend..."
echo "===================================="

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

echo "Starting Next.js development server..."
npm run dev

