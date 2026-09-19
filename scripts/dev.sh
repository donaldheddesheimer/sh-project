#!/usr/bin/env bash
# Run the backend and the frontend dev server together; Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x backend/.venv/bin/uvicorn || ! -d frontend/node_modules ]]; then
  echo "Dependencies missing - run 'make setup' first." >&2
  exit 1
fi

(cd backend && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000) &
backend=$!
npm --prefix frontend run dev &
frontend=$!
trap 'kill $backend $frontend 2>/dev/null' INT TERM EXIT

echo "Operations center: http://localhost:5173  (API: http://127.0.0.1:8000/docs)"
wait
