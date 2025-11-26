#!/bin/bash
# Start script for the FastAPI backend

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
BACKEND_DIR="$REPO_ROOT/apps/backend"

echo "Starting RUSH Policy RAG Backend..."
echo "=================================="

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

echo "Installing dependencies..."
pip install -q -r "$BACKEND_DIR/requirements.txt"

if [ -z "${SEARCH_API_KEY:-}" ]; then
    echo "Warning: SEARCH_API_KEY not set. Please configure your environment."
fi

cd "$BACKEND_DIR"
echo "Starting FastAPI server on port ${BACKEND_PORT:-8000}..."
python main.py

