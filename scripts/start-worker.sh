#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT/backend"
exec "$TASK_ROOT/.venv/bin/python" -m app.worker
