#!/usr/bin/env bash
# Start backend with correct PYTHONPATH for backend.xxx imports
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR"
cd "$SCRIPT_DIR/backend"
exec .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
