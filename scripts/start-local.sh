#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
[ -x .venv/bin/python ] || { echo 'Run ./scripts/setup-local.sh first.'; exit 1; }
./scripts/start-db.sh
(cd backend && "$TASK_ROOT/.venv/bin/alembic" upgrade head && "$TASK_ROOT/.venv/bin/python" app/initial_data.py)
TASK_BACKEND_PID=''
TASK_FRONTEND_PID=''
cleanup() {
    trap - EXIT INT TERM
    [ -z "$TASK_BACKEND_PID" ] || kill "$TASK_BACKEND_PID" 2>/dev/null || true
    [ -z "$TASK_FRONTEND_PID" ] || kill "$TASK_FRONTEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
(cd backend && exec "$TASK_ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8010 --workers 1) &
TASK_BACKEND_PID=$!
(cd frontend && exec bun run dev --host 127.0.0.1 --port 5178 --strictPort) &
TASK_FRONTEND_PID=$!
printf '%s\n' 'Open http://localhost:5178. Login credentials are in .env.' 'Ctrl+C stops the app; ./scripts/stop-local.sh also stops project PostgreSQL.'
# Portable to macOS bash 3 (no wait -n). Stop the other process if either exits.
while kill -0 "$TASK_BACKEND_PID" 2>/dev/null && kill -0 "$TASK_FRONTEND_PID" 2>/dev/null; do sleep 1; done
exit 1
