#!/usr/bin/env bash
# Start the backend from anywhere: backend/run.sh [--reload]
set -e
cd "$(dirname "$0")/.."
PY=python3
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python";
elif [ -x "$HOME/.venv-0815/bin/python" ]; then PY="$HOME/.venv-0815/bin/python"; fi
exec "$PY" -m uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
